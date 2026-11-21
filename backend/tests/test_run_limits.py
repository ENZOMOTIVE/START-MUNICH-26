"""Bounded live run: allowlist, counters, stop switch, model reservation, booking re-post."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db.models import ActionIntentRow, BookingRow, RfqRow, SessionRow
from app.platform import FakeGateway, FakeMailbox, outbox, runlimits, set_adapters
from app.platform.outbox import deliver_intent
from app.workflows.booking import confirm_booking
from app.workflows.extract import model_check


@pytest.fixture
def limits(monkeypatch):
    """Run limits on, with small caps and counters of their own (the test database persists)."""
    prefix = uuid4().hex[:8]
    settings = get_settings().model_copy(
        update={
            "run_limits": True,
            "run_max_rfqs": 2,
            "run_max_mails": 1,
            "run_max_model_calls": 2,
            "model_price_in_usd": 0.15,
            "model_price_out_usd": 0.60,
        }
    )
    monkeypatch.setattr(runlimits, "get_settings", lambda: settings)
    monkeypatch.setattr(outbox, "get_settings", lambda: settings)
    monkeypatch.setattr(runlimits, "_key", lambda name: f"t{prefix}:{name}"[:64])
    return settings


def _code(call, *args) -> str:
    with pytest.raises(DomainError) as refused:
        call(*args)
    return refused.value.code


def test_admission_is_capped_and_the_window_closes(db, limits, monkeypatch):
    runlimits.admit(db, "t1")
    runlimits.admit(db, "t1")  # admitting twice counts once
    runlimits.admit(db, "t2")
    assert _code(runlimits.admit, db, "t3") == "run_budget_exhausted"
    runlimits.require_admitted(db, "t1")
    assert _code(runlimits.require_admitted, db, "t3") == "not_admitted"

    start = runlimits._now_ms()
    monkeypatch.setattr(runlimits, "_now_ms", lambda: start + 61 * 60_000)
    assert _code(runlimits.admit, db, "t4") == "run_closed"
    runlimits.require_admitted(db, "t1")  # admitted work may still finish in the grace time
    monkeypatch.setattr(runlimits, "_now_ms", lambda: start + 66 * 60_000)
    assert _code(runlimits.require_admitted, db, "t1") == "run_closed"


def test_stop_ends_every_outbound_action(db, limits):
    runlimits.admit(db, "t1")
    runlimits.stop(db)
    assert _code(runlimits.require_admitted, db, "t1") == "run_stopped"
    assert _code(runlimits.admit, db, "t2") == "run_stopped"


def test_model_calls_are_reserved_before_they_are_made(db, limits, monkeypatch):
    assert runlimits.reserve_model_call(db, 1000, 200) == 271  # 150 + 120 micro-USD, rounded up
    runlimits.reserve_model_call(db, 1000, 200)
    assert _code(runlimits.reserve_model_call, db, 1000, 200) == "run_budget_exhausted"
    unpriced = limits.model_copy(update={"model_price_in_usd": 0.0})
    monkeypatch.setattr(runlimits, "get_settings", lambda: unpriced)
    assert _code(runlimits.reserve_model_call, db, 1, 1) == "model_price_unverified"


def _draft(db, rfq: RfqRow) -> ActionIntentRow:
    payload = {"to": "c@example.com", "subject": "s", "body": "b", "thread_id": rfq.thread_id}
    intent = ActionIntentRow(
        rfq_id=rfq.id, action_key=f"test:{uuid4()}", kind="reply", payload=payload
    )
    db.add(intent)
    db.flush()
    return intent


def test_dispatcher_sends_only_for_admitted_threads_and_counts_attempts(client, db, limits):
    session = db.get(SessionRow, UUID(client.post("/internal/demo/run").json()["session_id"]))
    rfq = db.get(RfqRow, session.rfq_id)
    first, second = _draft(db, rfq), _draft(db, rfq)

    assert _code(deliver_intent, db, first, FakeMailbox()) == "not_admitted"
    assert first.delivery_status == "pending"  # an old draft is kept, never released
    runlimits.admit(db, rfq.thread_id)
    assert deliver_intent(db, first, FakeMailbox()).delivery_status == "confirmed"
    assert _code(deliver_intent, db, second, FakeMailbox()) == "run_budget_exhausted"
    assert second.delivery_status == "pending"


def test_a_booking_the_portal_answered_is_not_posted_again(client, db):
    session = db.get(SessionRow, UUID(client.post("/internal/demo/run").json()["session_id"]))
    booking = db.query(BookingRow).filter(BookingRow.rfq_id == session.rfq_id).one()
    booking.status, booking.rate_id, booking.booking_reference = "submitted", "R-test", "BK-T"
    calls = []

    class Portal:
        def book(self, rate_id, team_reference):
            calls.append(team_reference)
            return {"booking_reference": "BK-T", "status": "submitted"}

    set_adapters({"mailbox": FakeMailbox(), "gateway": FakeGateway(), "booking": Portal()})
    try:
        confirm_booking(db, session)  # the live "booked" answer: wait for the airline's mail
        assert calls == []
        booking.booking_reference = None  # no answer on file: the identical request is repeated
        confirm_booking(db, session)
        assert calls == [booking.team_reference]
    finally:
        set_adapters(None)


class _Model:
    def __init__(self, text):
        self.text = text

    def complete(self, prompt, model, **params):
        return {"text": self.text}


def test_model_reading_is_compared_with_the_rules():
    ours = {
        "destination": "ORD",
        "weight_kg": Decimal("196"),
        "volume_m3": Decimal("0.72"),
        "pickup_required": True,
        "pickup_zone": "81",
        "service_intent": "cheapest",
    }
    same = (
        '{"destination": "ORD", "weight_kg": 196, "volume_cbm": 0.72, '
        '"pickup_postcode": "81311", "service": "cheapest"}'
    )
    assert model_check(_Model(same), "m", "mail", ours) == []
    two_lots = same.replace("196", "998").replace('"81311"', "null")
    assert model_check(_Model(two_lots), "m", "mail", ours) == ["weight_kg", "pickup_zone"]
    assert model_check(_Model("not json"), "m", "mail", ours) is None
