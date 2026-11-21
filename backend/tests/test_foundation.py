import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.core.errors import DomainError
from app.db.models import ActionIntentRow, MessageRow, RateCandidateRow, RfqRow, SessionRow
from app.db.repositories import events_for, first_quote_intent, quote_for
from app.db.session import get_engine
from app.platform.mailbox import FakeMailbox, ingest_message
from app.platform.outbox import deliver_intent
from app.platform.router import classify
from app.workflows.demo import run_demo
from app.workflows.rfq import commit_first_quote, handle_rfq
from sqlalchemy import select
from sqlalchemy.orm import Session as SASession


def _payload(**overrides) -> dict:
    stamp = uuid.uuid4().hex[:8]
    data = {
        "external_id": f"ext-{stamp}",
        "thread_id": f"thr-{stamp}",
        "sender": "shipper@example.com",
        "recipient": "desk@example.com",
        "subject": "RFQ FRA-LAX",
        "body": "Please quote RFQ FRA to LAX",
        "received_at": datetime.now(UTC),
        "classification": "rfq",
        "source": "test",
        "simulated": True,
        "raw": {
            "rfq": {
                "origin": "FRA",
                "destination": "LAX",
                "weight_kg": "250",  # the demo rate fixture is hand-checked for 250 kg only
                "volume_m3": "2.5",
                "pickup_required": False,
                "pickup_zone": None,
                "service_intent": "cheapest",
            },
            "simulated": True,
        },
    }
    data.update(overrides)
    return data


def _candidate(db, rfq: RfqRow) -> RateCandidateRow:
    row = RateCandidateRow(
        rfq_id=rfq.id,
        carrier="Yellowtail Air Freight",
        carrier_code="YT",
        total=Decimal("1250.00"),
        currency="EUR",
        transit_days=5,
        rate_id="YT-TEST",
        complete=True,
        source_ref="test",
        source_version="test",
        charges=[],
        simulated=True,
    )
    db.add(row)
    db.flush()
    return row


def test_demo_journey(db):
    session_id = run_demo(db)
    session = db.get(SessionRow, session_id)
    assert session is not None
    assert session.stage == "booked"
    events = events_for(db, session_id)
    types = [event.type for event in events]
    for required in (
        "message_received",
        "rates_loaded",
        "offer_selected",
        "quote_recorded",
        "customer_accepted",
        "booking_confirmed",
    ):
        assert required in types
    assert all(event.simulated for event in events)
    assert all(event.payload.get("simulated") is True for event in events)
    intents = list(
        db.execute(
            select(ActionIntentRow).where(
                ActionIntentRow.rfq_id == session.rfq_id,
                ActionIntentRow.kind == "first_quote",
            )
        ).scalars()
    )
    assert len(intents) == 1
    quote = quote_for(db, session.rfq_id)
    assert quote is not None
    selected = db.get(RateCandidateRow, quote.candidate_id)
    assert selected is not None
    assert "Yellowtail" in selected.carrier


def test_duplicate_intake(db):
    payload = _payload()
    first, created_first = ingest_message(db, payload)
    second, created_second = ingest_message(db, payload)
    assert first.id == second.id
    assert created_first is True
    assert created_second is False
    handle_rfq(db, first)
    handle_rfq(db, second)
    rfqs = list(db.execute(select(RfqRow).where(RfqRow.message_id == first.id)).scalars())
    assert len(rfqs) == 1
    intents = list(
        db.execute(
            select(ActionIntentRow).where(
                ActionIntentRow.rfq_id == rfqs[0].id,
                ActionIntentRow.kind == "first_quote",
            )
        ).scalars()
    )
    assert len(intents) == 1


def test_expired_rejects_quote(db):
    message, _ = ingest_message(db, _payload())
    past = datetime.now(UTC) - timedelta(minutes=1)
    rfq = RfqRow(
        message_id=message.id,
        thread_id=message.thread_id,
        origin="FRA",
        destination="LAX",
        weight_kg=Decimal("500"),
        service_intent="cheapest",
        received_at=past - timedelta(minutes=6),
        expires_at=past,
        status="pending",
    )
    db.add(rfq)
    db.flush()
    session = SessionRow(rfq_id=rfq.id, stage="pricing", deadline=past, simulated=True)
    db.add(session)
    db.flush()
    candidate = _candidate(db, rfq)
    with pytest.raises(DomainError) as raised:
        commit_first_quote(db, session, rfq, candidate)
    assert raised.value.code == "expired"
    assert first_quote_intent(db, rfq.id) is None


def test_pause_blocks_unsent(db):
    message, _ = ingest_message(db, _payload())
    now = datetime.now(UTC)
    rfq = RfqRow(
        message_id=message.id,
        thread_id=message.thread_id,
        origin="FRA",
        destination="LAX",
        weight_kg=Decimal("500"),
        service_intent="cheapest",
        received_at=now,
        expires_at=now + timedelta(minutes=6),
        status="pending",
    )
    db.add(rfq)
    db.flush()
    session = SessionRow(
        rfq_id=rfq.id, stage="pricing", deadline=rfq.expires_at, paused=True, simulated=True
    )
    db.add(session)
    db.flush()
    candidate = _candidate(db, rfq)
    with pytest.raises(DomainError) as raised:
        commit_first_quote(db, session, rfq, candidate)
    assert raised.value.code == "session_paused"
    assert first_quote_intent(db, rfq.id) is None


def test_state_survives_new_session(db):
    session_id = run_demo(db)
    db.commit()
    other = SASession(bind=get_engine())
    try:
        row = other.get(SessionRow, session_id)
        assert row is not None
        assert row.stage == "booked"
    finally:
        other.close()


def test_unknown_no_blind_resend(db):
    message, _ = ingest_message(db, _payload())
    now = datetime.now(UTC)
    rfq = RfqRow(
        message_id=message.id,
        thread_id=message.thread_id,
        origin="FRA",
        destination="LAX",
        weight_kg=Decimal("500"),
        service_intent="cheapest",
        received_at=now,
        expires_at=now + timedelta(minutes=6),
        status="quoted",
    )
    db.add(rfq)
    db.flush()
    intent = ActionIntentRow(
        rfq_id=rfq.id,
        action_key=f"quote:{rfq.id}",
        kind="first_quote",
        payload={"simulated": True},
        delivery_status="pending",
        simulated=True,
    )
    db.add(intent)
    db.flush()
    intent.delivery_status = "UNKNOWN"
    db.flush()
    with pytest.raises(DomainError) as raised:
        deliver_intent(db, intent, FakeMailbox())
    assert raised.value.code == "unknown_no_blind_resend"
    db.refresh(intent)
    assert intent.delivery_status == "UNKNOWN"


def test_classify_acceptance_despite_rfq_subject():
    row = MessageRow(
        external_id="x",
        thread_id="t",
        sender="buyer@example.com",
        subject="RE: RFQ FRA-LAX",
        body="we accept your offer",
        received_at=datetime.now(UTC),
        classification="acceptance",
    )
    assert classify(row) == "acceptance"


def test_demo_mode_no_live_http(db, monkeypatch):
    def fail(self, *args, **kwargs):
        raise AssertionError("live HTTP is not allowed in demo")

    monkeypatch.setattr(httpx.Client, "request", fail)
    session_id = run_demo(db)
    session = db.get(SessionRow, session_id)
    assert session is not None
    assert session.stage == "booked"
