import json
import uuid
from contextlib import contextmanager

import httpx
import pytest
from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db import repositories
from app.db.models import JobRow, MailboxCursorRow, MessageRow
from app.platform import mailbox as mailbox_module
from app.platform.mailbox import (
    LiveMailbox,
    MailboxError,
    MailboxPoller,
    map_poll_item,
    poll_mailbox_once,
    poll_page,
)
from sqlalchemy import func, select


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("LIVE_ACTIONS_ENABLED", "true")
    monkeypatch.setenv("HACKATHON_API_KEY", "hk_test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.example.test",
        headers={"X-API-Key": "hk_test"},
        transport=httpx.MockTransport(handler),
    )


def _item(mail_id: int, references=None, **extra) -> dict:
    return {
        "id": mail_id,
        "from": "buyer@shipper.example",
        "to": "free-candy@hackloop.mail",
        "subject": "Quote please",
        "body": "FRA to LAX, 250 kg",
        "references": references or [],
        "attachments": [],
        **extra,
    }


class StubMailbox:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def poll(self, since_id, limit=100):
        self.calls.append(since_id)
        page = self.pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def test_poll_sends_cursor_and_key(live):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("X-API-Key")
        return httpx.Response(200, json={"messages": [_item(5)], "next_since_id": 5})

    page = LiveMailbox(_client(handler)).poll(4, limit=50)
    assert seen["url"].endswith("/v1/mailbox/messages?since_id=4&limit=50")
    assert seen["key"] == "hk_test"
    assert poll_page(page) == ([_item(5)], 5)


def test_poll_empty_page(live):
    mailbox = LiveMailbox(_client(lambda r: httpx.Response(200, json={"next_since_id": 7})))
    assert poll_page(mailbox.poll(7)) == ([], 7)


@pytest.mark.parametrize(
    ("response", "kind", "retry_after"),
    [
        (httpx.Response(429, headers={"Retry-After": "12"}), "rate_limited", 12.0),
        (httpx.Response(429), "rate_limited", None),
        (httpx.Response(500), "http_error", None),
    ],
)
def test_poll_errors(live, response, kind, retry_after):
    with pytest.raises(MailboxError) as raised:
        LiveMailbox(_client(lambda r: response)).poll(0)
    assert raised.value.kind == kind
    assert raised.value.retry_after == retry_after


def test_poll_network_error(live):
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(MailboxError) as raised:
        LiveMailbox(_client(handler)).poll(0)
    assert raised.value.kind == "network"


def test_poll_refused_in_demo():
    get_settings.cache_clear()
    with pytest.raises(LiveActionsDisabled):
        LiveMailbox(_client(lambda r: httpx.Response(200, json={}))).poll(0)


def test_download_attachment(live):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/mailbox/messages/12/attachments/3"
        return httpx.Response(200, content=b"%PDF-1.7")

    assert LiveMailbox(_client(handler)).download_attachment(12, 3) == b"%PDF-1.7"


def test_download_attachment_refused_in_demo():
    get_settings.cache_clear()

    def handler(request):
        raise AssertionError("no request in demo")

    with pytest.raises(LiveActionsDisabled):
        LiveMailbox(_client(handler)).download_attachment(12, 3)


def test_map_thread_starter_and_reply():
    starter = map_poll_item(_item(40))
    reply = map_poll_item(_item(41, references=[40], subject="Re: Quote please"))
    assert (starter["external_id"], starter["thread_id"]) == ("40", "40")
    assert (reply["external_id"], reply["thread_id"]) == ("41", "40")
    assert reply["raw"] == {"mail": _item(41, references=[40], subject="Re: Quote please")}
    assert reply["simulated"] is False
    assert starter["sender"] == "buyer@shipper.example"


def _cursor_name(monkeypatch) -> str:
    name = f"test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(mailbox_module, "CURSOR_NAME", name)
    return name


def _base_id() -> int:
    return 10_000_000 + uuid.uuid4().int % 1_000_000_000


def test_redelivered_page_creates_no_duplicates(db, monkeypatch):
    cursor = _cursor_name(monkeypatch)
    base = _base_id()
    items = [_item(base), _item(base + 1, references=[base])]
    page = {"messages": items, "next_since_id": base + 1}
    assert poll_mailbox_once(db, StubMailbox([page])) == 2
    # Simulate a crash before the cursor moved: the same page is delivered again.
    db.get(MailboxCursorRow, cursor).since_id = 0
    db.flush()
    assert poll_mailbox_once(db, StubMailbox([json.loads(json.dumps(page))])) == 2
    ids = [str(base), str(base + 1)]
    messages = db.execute(
        select(func.count()).select_from(MessageRow).where(MessageRow.external_id.in_(ids))
    ).scalar_one()
    rows = db.execute(select(MessageRow.id).where(MessageRow.external_id.in_(ids))).scalars()
    jobs = db.execute(
        select(func.count())
        .select_from(JobRow)
        .where(JobRow.payload["message_id"].astext.in_([str(r) for r in rows]))
    ).scalar_one()
    assert (messages, jobs) == (2, 2)
    assert repositories.get_cursor(db, cursor) == base + 1


def test_cursor_falls_back_to_max_id(db, monkeypatch):
    cursor = _cursor_name(monkeypatch)
    base = _base_id()
    poll_mailbox_once(db, StubMailbox([[_item(base), _item(base + 3)]]))
    assert repositories.get_cursor(db, cursor) == base + 3


def _scope_for(db):
    @contextmanager
    def scope():
        savepoint = db.begin_nested()
        try:
            yield db
            savepoint.commit()
        except Exception:
            savepoint.rollback()
            raise

    return scope


def test_failed_poll_leaves_cursor_unchanged(db, monkeypatch):
    cursor = _cursor_name(monkeypatch)
    repositories.advance_cursor(db, cursor, 5)
    poller = MailboxPoller(StubMailbox([MailboxError("http_error", "HTTP 500")]), 1.5, lambda: 0)
    assert poller.tick(_scope_for(db)) is None
    assert repositories.get_cursor(db, cursor) == 5


def test_failure_mid_page_rolls_back_whole_page(db, monkeypatch):
    cursor = _cursor_name(monkeypatch)
    base = _base_id()
    page = {"messages": [_item(base), {"no_id": True}], "next_since_id": base + 1}
    poller = MailboxPoller(StubMailbox([page]), 1.5, lambda: 0)
    assert poller.tick(_scope_for(db)) is None
    assert repositories.get_cursor(db, cursor) == 0
    stored = db.execute(
        select(func.count()).select_from(MessageRow).where(MessageRow.external_id == str(base))
    ).scalar_one()
    assert stored == 0


def test_rate_limit_delays_next_poll(db, monkeypatch):
    _cursor_name(monkeypatch)
    now = [100.0]
    stub = StubMailbox([MailboxError("rate_limited", "HTTP 429", 30.0), {"next_since_id": 0}])
    poller = MailboxPoller(stub, 1.5, lambda: now[0])
    poller.tick(_scope_for(db))
    now[0] = 110.0
    assert poller.tick(_scope_for(db)) is None
    assert len(stub.calls) == 1
    now[0] = 131.0
    assert poller.tick(_scope_for(db)) == 0
    assert len(stub.calls) == 2


def test_poll_interval_is_respected(db, monkeypatch):
    _cursor_name(monkeypatch)
    now = [0.0]
    stub = StubMailbox([{"next_since_id": 0}, {"next_since_id": 0}])
    poller = MailboxPoller(stub, 1.5, lambda: now[0])
    poller.tick(_scope_for(db))
    now[0] = 1.0
    poller.tick(_scope_for(db))
    assert len(stub.calls) == 1
    now[0] = 1.6
    poller.tick(_scope_for(db))
    assert len(stub.calls) == 2


# Shape captured from a live poll on 2026-11-21 (mail 267); body shortened.
LIVE_ITEM = {
    "id": 267,
    "from": "lindqvist-medtech@hackloop.mail",
    "to": "free-candy@hackloop.mail",
    "subject": "Fastest air freight to ORD",
    "body": "Hi,\n\nCould you quote an all-in price in EUR for the fastest air freight option "
    "to ORD (Chicago, US)? ... 676 kg gross and 5.79 cbm.\n\nSara\nLindqvist Medtech",
    "in_reply_to": None,
    "references": [],
    "received_at": "2026-11-21T15:00:59.986067+00:00",
    "attachments": [],
}


def test_map_captured_live_item_and_reply():
    from datetime import UTC, datetime

    starter = map_poll_item(LIVE_ITEM)
    assert starter["external_id"] == "267"
    assert starter["thread_id"] == "267"
    assert starter["sender"] == "lindqvist-medtech@hackloop.mail"
    assert starter["recipient"] == "free-candy@hackloop.mail"
    assert starter["subject"] == "Fastest air freight to ORD"
    assert starter["body"].startswith("Hi,")
    assert starter["received_at"] == datetime(2026, 11, 21, 15, 0, 59, 986067, tzinfo=UTC)
    assert starter["raw"] == {"mail": LIVE_ITEM}

    reply = map_poll_item({**LIVE_ITEM, "id": 1301, "in_reply_to": 267, "references": [267]})
    assert (reply["external_id"], reply["thread_id"]) == ("1301", "267")
    assert poll_page({"messages": [LIVE_ITEM], "next_since_id": 1225}) == ([LIVE_ITEM], 1225)
