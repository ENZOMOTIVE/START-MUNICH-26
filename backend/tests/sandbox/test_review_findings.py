"""Regression tests for the defects found in the review of commit 3fdda69."""

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from app.core.config import Settings
from app.core.errors import DomainError, LiveActionsDisabled
from app.db.models import JobRow
from app.db.session import get_session_factory
from app.platform import jobs
from app.platform.mailbox import poll_mailbox_once
from app.platform.readonly import ObservationOnly, ReadOnlyTransport
from app.pricing.fixtures import candidates_from_fixtures
from app.pricing.portal_client import PortalClient, PortalError, PortalState
from app.sandbox import runner
from app.sandbox.scenarios import PORTAL, rfq, scenario
from app.workflows import replies
from app.workflows.booking import acceptance_problem

ROOT = Path(__file__).resolve().parents[3]
QUOTE = (Decimal("500.00"), "EUR", "FRA", "ORD")
# 100 kg x 5.00 = EUR 500.00, the only carrier on this portal.
PORTAL_500 = {
    **PORTAL,
    "lanes": {
        "FRA-ORD": [
            {
                "code": "FI",
                "name": "Five Hundred Air",
                "min": "50.00",
                "transit": 2,
                "per_kg": {0: "5.00"},
            }
        ]
    },
}


class _Settings:
    """Stands in for get_settings() where one value has to differ."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def live_allowed(self) -> bool:
        return False


# ---------- acceptance must match the quote exactly ----------


@pytest.mark.parametrize(
    "reply,problem",
    [
        ("We accept if the surcharge is removed.", "conditional_or_deferred"),
        ("Please book only after I confirm tomorrow.", "conditional_or_deferred"),
        ("We accept your quote of EUR 100.", "amount_differs_from_quote"),
        (
            "We accept your EUR 500 quote, but change the destination to SFO.",
            "route_differs_from_quote",
        ),
        ("We accept your quote of USD 500.", "amount_differs_from_quote"),
        ("We accept your EUR 500 quote with express service.", "service_differs_from_quote"),
        ("Accepted, please send it overnight.", "service_differs_from_quote"),
        ("We accept your EUR 500 quote.", None),
        ("We accept the quote. Please go ahead and book.", None),
    ],
)
def test_only_an_acceptance_of_the_quote_as_sent_passes(reply, problem):
    assert acceptance_problem(reply, *QUOTE) == problem


@pytest.mark.parametrize(
    "reply",
    [
        "We accept if the surcharge is removed.",
        "Please book only after I confirm tomorrow.",
        "We accept your quote of EUR 100.",
        "We accept your EUR 500 quote, but change the destination to SFO.",
    ],
)
def test_a_changed_or_conditional_reply_never_books(reply, db):
    customer = {"budget": "600.00", "truth": ["FRA", "ORD", "100 kg"], "accept_text": reply}
    case = scenario(
        "acceptance_guard",
        [rfq(body="Please quote 100 kg from FRA to ORD.", customer=customer)],
        {},
        portal=PORTAL_500,
    )
    result = runner.run(case, 42)
    request = result["requests"]["rfq1"]
    assert request["quote"]["total"] == "500.00"
    assert request["booking"] is None and request["stage"] == "quoted"
    assert result["totals"]["booking_requests"] == 0
    assert "booking_confirmed" not in request["outbound"]
    assert "customer_accepted" not in request["events"]


def test_an_unconditional_acceptance_of_the_same_quote_books(db):
    customer = {
        "budget": "600.00",
        "truth": ["FRA", "ORD", "100 kg"],
        "accept_text": "We accept your quote of EUR 500.00. Please book it.",
    }
    case = scenario(
        "acceptance_positive",
        [rfq(body="Please quote 100 kg from FRA to ORD.", customer=customer)],
        {},
        portal=PORTAL_500,
    )
    request = runner.run(case, 42)["requests"]["rfq1"]
    assert (request["stage"], request["booking"]["status"]) == ("booked", "confirmed")
    assert request["quote"]["total"] == "500.00"


# ---------- pricing ----------


def test_a_fixed_fixture_price_is_not_offered_for_another_weight():
    def offers(kg):
        request = {
            "id": str(uuid.uuid4()),
            "origin": "FRA",
            "destination": "LAX",
            "weight_kg": kg,
            "volume_m3": None,
            "pickup_required": True,
            "pickup_zone": "60",
            "service_intent": "cheapest",
        }
        return candidates_from_fixtures(request, fixtures_dir=ROOT / "fixtures")

    assert all(c.complete for c in offers("250")) and len(offers("250")) == 3
    assert not any(c.complete for c in offers("800"))


def test_portal_lanes_and_breaks_are_fetched_once_for_many_requests(db):
    before = runner.run("paging_250", 42, share_portal_cache=False)["totals"]["portal_requests"]
    after = runner.run("paging_250", 42)
    assert after["passed"]
    # Two requests on the same lane: 1 lanes call + 3 breaks calls are saved the second time.
    assert before - after["totals"]["portal_requests"] == 4
    assert after["totals"]["portal_cache_hits"] >= 4


def test_no_request_is_sent_while_the_portal_budget_is_exhausted():
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={"lanes": []})

    async def attempt():
        http = httpx.AsyncClient(
            base_url="https://sandbox.invalid", transport=httpx.MockTransport(handler)
        )
        state = PortalState()
        state.rate_remaining = 0
        state.window_reset = time.monotonic() + 600  # the portal said: wait ten minutes
        async with PortalClient(http=http, state=state) as client:
            with pytest.raises(PortalError) as raised:
                await client.rate("FRA", "ORD", "AL", 300, 0)
            return raised.value.kind

    assert asyncio.run(attempt()) == "rate_limited"
    assert sent == []


# ---------- intake and retries ----------


def test_only_one_poller_polls_at_a_time(db):
    class Mailbox:
        polls = 0

        def poll(self, since_id, limit=100):
            self.polls += 1
            return {"messages": [], "next_since_id": since_id}

    first, second = Mailbox(), Mailbox()
    poll_mailbox_once(db, first)  # this transaction now holds the poller lock
    with get_session_factory()() as other:
        assert poll_mailbox_once(other, second) == 0
    assert (first.polls, second.polls) == (1, 0)


def test_a_failed_job_is_not_retried_in_the_same_cycle(db, monkeypatch):
    from app.core.errors import DomainError

    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    retry = JobRow(
        kind="route_message",
        payload={"message_id": str(uuid.uuid4())},
        created_at=long_ago,
        run_after=long_ago,
    )
    db.add(retry)
    db.flush()

    def temporarily_failing(_db, _message_id):
        raise DomainError("send_failed")

    monkeypatch.setattr(jobs, "route_message", temporarily_failing)
    processed = jobs.process_jobs_once(db, "worker-1", limit=5)
    ours = [j for j in processed if j.id == retry.id]
    assert len(ours) == 1 and (retry.status, retry.attempts) == ("pending", 1)
    assert retry.run_after > retry.claimed_at  # backed off: not claimable again right now

    def permanent(_db, _message_id):
        raise DomainError("invalid_weight")

    retry.run_after = long_ago
    monkeypatch.setattr(jobs, "route_message", permanent)
    jobs.process_jobs_once(db, "worker-1", limit=5)
    assert retry.status == "failed" and retry.attempts == 2  # bad input is not retried


def test_mail_no_agent_handles_is_left_for_a_person(db):
    case = scenario(
        "unsupported_mail",
        [
            {
                "key": "desk",
                "from": "ratedesk@hackloop.mail",
                "subject": "Rate desk answer",
                "body": "Here is the answer to your question.",
            }
        ],
        {},
    )
    result = runner.run(case, 42)
    assert result["totals"]["mails_stored"] == 1
    from app.db.models import MessageRow

    newest = db.query(MessageRow).filter(MessageRow.sender == "ratedesk@hackloop.mail")
    flagged = newest.order_by(MessageRow.created_at.desc()).first()
    assert flagged.classification == "rate_desk_reply"
    assert flagged.raw["unmatched"]["reason"] == "no agent handles this kind of mail"


# ---------- operator takeover ----------


def test_operator_edits_and_sends_a_draft_through_the_outbox(client, db):
    session_id = client.post("/internal/demo/run").json()["session_id"]
    actions = client.get(f"/api/sessions/{session_id}/actions").json()
    assert {a["delivery_status"] for a in actions} <= {"confirmed", "pending", "sending"}
    sent = next(a for a in actions if a["kind"] == "first_quote")
    url = f"/api/sessions/{session_id}/actions/{sent['id']}"

    # Not taken over yet: nothing may be changed or sent by hand.
    assert client.put(url, json={"body": "x"}).status_code == 409
    client.post(f"/api/sessions/{session_id}/pause")
    # Already delivered: it is not a draft any more, and it cannot be sent twice.
    assert client.put(url, json={"body": "x"}).json()["error"]["code"] == "not_a_draft"
    again = client.post(f"{url}/send").json()
    assert again["delivery_status"] == "confirmed"
    assert client.post(f"{url}/resolve", json={"sent": True}).json()["error"]["code"] == (
        "not_unknown"
    )
    client.post(f"/api/sessions/{session_id}/resume")


def test_operator_resolves_an_unknown_send(client, db):
    from app.db.models import ActionIntentRow

    session_id = client.post("/internal/demo/run").json()["session_id"]
    quote = next(
        a
        for a in client.get(f"/api/sessions/{session_id}/actions").json()
        if a["kind"] == "first_quote"
    )
    intent = db.get(ActionIntentRow, uuid.UUID(quote["id"]))
    original = intent.delivery_status
    intent.delivery_status = "UNKNOWN"
    db.flush()
    client.post(f"/api/sessions/{session_id}/pause")
    url = f"/api/sessions/{session_id}/actions/{quote['id']}"
    assert client.post(f"{url}/send").json()["error"]["code"] == "unknown_no_blind_resend"
    resolved = client.post(f"{url}/resolve", json={"sent": True}).json()
    assert resolved["delivery_status"] == "confirmed"
    client.post(f"/api/sessions/{session_id}/resume")
    intent.delivery_status = original


# ---------- customer replies follow the voice guide ----------


def test_every_customer_mail_follows_the_voice_guide():
    guide = (ROOT / "docs" / "agent-human-voice.md").read_text()
    avoid = guide.split("## Avoid")[1].split("## Formatting")[0]
    banned = [phrase.lower() for phrase in re.findall(r'"([^"]+)"', avoid)]
    assert "rest assured" in banned and "kindly note" in banned
    when = datetime(2026, 11, 21, 9, 15)
    mails = [
        replies.missing_info("Anna", "RFQ", "FRA", "ORD", ["weight_kg", "pickup_zone"], []),
        replies.dg_documents("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd", "msds"]),
        replies.dg_conflict("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd"]),
        replies.final_quote(
            "Anna",
            "RFQ",
            "FRA",
            "ORD",
            "300",
            "Bravo Freight",
            "585.00",
            "EUR",
            5,
            ["air_all_in"],
            when,
        ),
        replies.acceptance_clarification(
            "Anna", "RFQ", "Bravo Freight", "amount_differs_from_quote"
        ),
        replies.no_offer("Anna", "RFQ", "FRA", "ORD", "no carrier returned a complete rate"),
        replies.booking_submitted("Anna", "RFQ", "Bravo Freight", "TM-1"),
        replies.booking_confirmed(
            "Anna", "RFQ", "Bravo Freight", "BK-1", "FRA", "ORD", "300", "585.00", "EUR"
        ),
        replies.booking_failed("Anna", "RFQ", "Bravo Freight", "the rate expired"),
        replies.tracking_answer("Anna", "RFQ", "BK-1", "confirmed", "Bravo Freight", None),
    ]
    for mail in mails:
        body = mail["body"]
        assert body.startswith("Hello Anna,") and body.endswith("FiveU Freight Desk")
        assert "!" not in body and len(body) < 700, mail["purpose"]
        assert not [phrase for phrase in banned if phrase in body.lower()], mail["purpose"]
    hold = mails[4]["body"]
    assert "have not booked anything yet" in hold and "EUR" not in hold


# ---------- observation mode: reads only ----------

OBSERVE = {
    "app_mode": "observe",
    "database_url": "postgresql+psycopg://u:p@127.0.0.1:5434/fiveu_observe",
}


def test_observation_mode_refuses_unsafe_configuration():
    assert Settings(_env_file=None, **OBSERVE).live_allowed() is False
    for bad, message in (
        ({"live_actions_enabled": True}, "LIVE_ACTIONS_ENABLED must be false"),
        ({"llm_mode": "gateway"}, "no paid calls"),
        ({"database_url": "postgresql+psycopg://u:p@127.0.0.1:5433/fiveu"}, "its own database"),
    ):
        with pytest.raises(ValueError, match=message):
            Settings(_env_file=None, **{**OBSERVE, **bad})


def test_read_only_transport_refuses_every_write_before_dispatch():
    reached = []

    def remote(request):
        reached.append(request.method)
        return httpx.Response(200, json={"messages": []})

    transport = ReadOnlyTransport(httpx.MockTransport(remote))
    with httpx.Client(base_url="https://sandbox.invalid", transport=transport) as client:
        assert client.get("/v1/mailbox/messages").status_code == 200
        for method, path in (
            ("POST", "/v1/mailbox/messages"),
            ("POST", "/v1/portal/bookings"),
            ("PUT", "/v1/mailbox/webhook"),
            ("DELETE", "/v1/mailbox/messages/1"),
            ("POST", "/v1/gateway/chat/completions"),
            ("GET", "/v1/gateway/models"),
        ):
            with pytest.raises(ObservationOnly):
                client.request(method, path)
    assert reached == ["GET"] and len(transport.refused) == 6


def test_observation_mode_reads_mail_but_cannot_send_book_or_call_a_model(monkeypatch):
    import app.platform.booking as booking
    import app.platform.gateway as gateway
    import app.platform.mailbox as mailbox

    observe = Settings(_env_file=None, **OBSERVE)
    for module in (mailbox, booking, gateway):
        monkeypatch.setattr(module, "get_settings", lambda: observe)
    reached = []

    def remote(request):
        reached.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"messages": [], "next_since_id": 0})

    client = httpx.Client(base_url="https://sandbox.invalid", transport=httpx.MockTransport(remote))
    box = mailbox.LiveMailbox(client)
    assert box.poll(0)["messages"] == []
    with pytest.raises(ObservationOnly):
        box.send({"to": "customer@example.com", "subject": "Quote", "body": "EUR 1.00"})
    with pytest.raises(LiveActionsDisabled):
        booking.PortalBooking(client).book("R-1", "TM-1")
    with pytest.raises(LiveActionsDisabled):
        gateway.LiveGateway(client=client).complete("hello", "any-model")
    assert reached == ["GET /v1/mailbox/messages"]


def test_observed_proposals_are_never_sent_even_after_the_mode_changes(db, monkeypatch):
    import app.workflows.rfq as rfq_flow
    from app.db.models import ActionIntentRow
    from app.platform.outbox import deliver_intent, deliver_pending

    with monkeypatch.context() as observing:
        observing.setattr(rfq_flow, "get_settings", lambda: _Settings(app_mode="observe"))
        result = runner.run("happy_path", 42)
    assert result["totals"]["mails_sent"] == 0  # the quote was proposed, not sent
    proposal = (
        db.query(ActionIntentRow)
        .filter(ActionIntentRow.delivery_status == "observed")
        .order_by(ActionIntentRow.created_at.desc())
        .first()
    )
    assert proposal.kind == "first_quote" and "EUR 585.00" in proposal.payload["body"]

    class Mailbox:
        sent = 0

        def send(self, payload):
            self.sent += 1
            return {"id": 1}

    live = Mailbox()  # the mode is no longer observe here
    deliver_pending(db, live, [proposal.payload["thread_id"]])
    with pytest.raises(DomainError) as refused:
        deliver_intent(db, proposal, live)
    assert refused.value.code == "observation_only" and live.sent == 0
    assert proposal.delivery_status == "observed"


# ---------- one worker, one budget ----------


def test_a_second_worker_refuses_to_start(db):
    from app.workers.main import WORKER_LOCK, claim_single_worker
    from sqlalchemy import text

    first = claim_single_worker()
    try:
        with pytest.raises(SystemExit, match="run exactly one"):
            claim_single_worker()
    finally:
        first.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
        first.close()


def test_parallel_requests_never_exceed_the_reported_portal_budget():
    sent = []

    def remote(request):
        sent.append(request.url.params["airline"])
        headers = {"X-RateLimit-Limit": "10", "X-RateLimit-Remaining": str(2 - len(sent))}
        return httpx.Response(200, headers=headers, json={"rate_id": "R-1"})

    async def three_at_once():
        http = httpx.AsyncClient(
            base_url="https://sandbox.invalid", transport=httpx.MockTransport(remote)
        )
        state = PortalState()
        state.rate_remaining, state.seen_at = 2, time.monotonic()
        async with PortalClient(http=http, state=state) as client:
            calls = (client.rate("FRA", "ORD", code, 300, 0) for code in ("AA", "BB", "CC"))
            return await asyncio.gather(*calls, return_exceptions=True)

    results = asyncio.run(three_at_once())
    refused = [r for r in results if isinstance(r, PortalError)]
    assert len(sent) == 2 and [r.kind for r in refused] == ["rate_limited"]


def test_two_senders_of_one_action_deliver_it_once(db):
    import threading

    from app.core import clock
    from app.db.models import ActionIntentRow
    from app.platform.outbox import deliver_intent

    case = scenario("never_sent", [rfq()], {}, faults={"mailbox_send": ["not_sent"] * 40})
    runner.run(case, 42, until_s=2)
    intent_id = (
        db.query(ActionIntentRow.id)
        .filter(ActionIntentRow.kind == "first_quote", ActionIntentRow.delivery_status == "pending")
        .order_by(ActionIntentRow.created_at.desc())
        .first()[0]
    )
    db.commit()

    class Mailbox:
        sent = 0

        def send(self, payload):
            Mailbox.sent += 1
            return {"id": 7}

    start, outcomes = threading.Barrier(2), []

    def sender():
        with get_session_factory()() as own:
            intent = own.get(ActionIntentRow, intent_id)
            assert intent.delivery_status == "pending"  # both read it before either claims
            start.wait()
            try:
                deliver_intent(own, intent, Mailbox())
                own.commit()
                outcomes.append("sent")
            except DomainError as exc:
                own.commit()
                outcomes.append(exc.code)

    clock.set_clock(lambda: datetime(2026, 11, 21, 9, 0, 5, tzinfo=UTC))  # inside the deadline
    try:
        threads = [threading.Thread(target=sender) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        clock.set_clock(None)
    assert sorted(outcomes) == ["send_in_progress", "sent"] and Mailbox.sent == 1


# ---------- other seeds and unseen wording ----------


def test_every_scenario_passes_on_other_seeds(db):
    from app.sandbox.scenarios import SCENARIOS

    failed = []
    for seed in (7, 99, 2026):
        for name in SCENARIOS:
            result = runner.run(name, seed)
            failed += [(name, seed, c) for c in result["checks"] if not c["passed"]]
    assert not failed


@pytest.mark.parametrize(
    "body",
    [
        "Kindly quote 300kg, 1.2cbm, ORD ex FRA.",
        "Rate request: FRA → ORD, 300 kgs, 1.2 m3",
        "Need a price for 0.3 tonnes from Frankfurt (FRA) to Chicago (ORD), 1.2 cubic metres.",
        "Hi team,\nour shipment: 300 KG / 1,2 CBM\nRoute FRA-ORD\nPlease send your best rate.\nTom",
        "Guten Tag, bitte um ein Angebot: 300 kg, 1,2 cbm, von FRA nach ORD. Danke",
        "pls quote\n\ndest: ORD\norigin: FRA\ngross weight: 300 kg\nvolume: 1.2 cbm",
    ],
)
def test_unseen_wording_is_read_and_quoted_the_same(body, db):
    request = runner.run(scenario("wording", [rfq(body=body)], {}), 42)["requests"]["rfq1"]
    assert request["extracted"]["method"] == "rules"
    assert (request["extracted"]["origin"], request["extracted"]["destination"]) == ("FRA", "ORD")
    assert request["quote"]["total"] == "585.00" and request["stage"] == "booked"
