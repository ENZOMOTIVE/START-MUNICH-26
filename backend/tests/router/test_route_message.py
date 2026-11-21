import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.db.models import (
    JobRow,
    MessageRow,
    QuoteDecisionRow,
    RateCandidateRow,
    RfqRow,
    SessionRow,
)
from app.db.repositories import events_for
from app.platform.decisions import ACCEPT, QUESTION, ReplyContext, ReplyJudgment
from app.platform.jobs import process_jobs_once
from app.platform.mailbox import ingest_message
from app.platform.router import route_message
from sqlalchemy import func, select

AIRLINE = "aurelia-air-cargo@hackloop.mail"
TRUCKER = "Some Trucker <{}>"


class FixedJudge:
    def __init__(self, verdict: str):
        self.verdict = verdict

    def judge(self, reply: ReplyContext) -> ReplyJudgment:
        return ReplyJudgment(
            verdict=self.verdict,
            decided_by="gateway",
            model="test-model",
            probabilities={"A": 0.91, "Q": 0.06, "D": 0.03},
        )


def _ingest(db, **overrides) -> MessageRow:
    stamp = uuid.uuid4().hex[:10]
    payload = {
        "external_id": f"ext-{stamp}",
        "thread_id": f"thr-{stamp}",
        "sender": "buyer@shipper.example",
        "subject": "Quote please",
        "body": "Please quote",
        "received_at": datetime.now(UTC),
        "source": "test",
        "simulated": True,
        "raw": {},
    }
    payload.update(overrides)
    message, _ = ingest_message(db, payload)
    return message


def _quoted_thread(db) -> tuple[str, SessionRow]:
    rfq_mail = _ingest(db, classification="rfq")
    now = datetime.now(UTC)
    rfq = RfqRow(
        message_id=rfq_mail.id,
        thread_id=rfq_mail.thread_id,
        origin="FRA",
        destination="LAX",
        weight_kg=Decimal("250"),
        service_intent="cheapest",
        received_at=now,
        expires_at=now + timedelta(minutes=6),
        status="quoted",
    )
    db.add(rfq)
    db.flush()
    candidate = RateCandidateRow(
        rfq_id=rfq.id,
        carrier="Yellowtail Air Freight",
        total=Decimal("412.50"),
        currency="EUR",
        transit_days=5,
        rate_id="R-TEST",
        complete=True,
        source_ref="test",
        source_version="test",
        charges=[],
    )
    db.add(candidate)
    db.flush()
    db.add(
        QuoteDecisionRow(
            rfq_id=rfq.id,
            candidate_id=candidate.id,
            total=Decimal("412.50"),
            currency="EUR",
            decided_at=now,
        )
    )
    session = SessionRow(rfq_id=rfq.id, stage="quoted", deadline=rfq.expires_at)
    db.add(session)
    db.flush()
    return rfq_mail.thread_id, session


def _reply(db, thread_id: str, body: str, **overrides) -> MessageRow:
    return _ingest(
        db,
        thread_id=thread_id,
        subject="Re: Quote please",
        body=body,
        received_at=datetime.now(UTC) + timedelta(seconds=1),
        **overrides,
    )


def _routing(message: MessageRow) -> dict:
    return message.raw["routing"]


@pytest.mark.parametrize(
    ("sender", "attachments", "kind"),
    [
        ("tracking@hackloop.mail", False, "tracking"),
        ("ratedesk@hackloop.mail", False, "rate_desk_reply"),
        (AIRLINE, False, "carrier_bid"),
        (AIRLINE, True, "tariff"),
    ],
)
def test_sender_kinds_are_stored(db, sender, attachments, kind):
    raw = {"mail": {"id": 1, "references": [], "attachments": [{"id": 9}] if attachments else []}}
    message = _ingest(db, sender=sender, raw=raw)
    route_message(db, message.id)
    assert message.classification == kind
    record = _routing(message)
    assert record["kind"] == kind
    assert record["decided_by"] == "sender_rule"
    assert record["rule"]


def test_booking_confirmation_is_stored(db):
    message = _ingest(db, sender=AIRLINE, subject="Booking BK-ABCD1234 confirmed")
    route_message(db, message.id)
    assert message.classification == "booking_confirmation"


def test_trucker_bid_records_trucker(db):
    from app.platform.carriers import load_carriers

    trucker = next(c for c in load_carriers().values() if c.kind == "trucker")
    message = _ingest(db, sender=TRUCKER.format(trucker.email))
    route_message(db, message.id)
    record = _routing(message)
    assert record["kind"] == "carrier_bid"
    assert record["sender"] == {"email": trucker.email, "name": trucker.name, "kind": "trucker"}
    assert record["agent"] == "rfq_booking"


def test_customer_new_thread_is_rfq_and_dispatched(db):
    message = _ingest(db, raw={"mail": {"id": 2, "references": []}})
    from app.core.errors import DomainError

    with pytest.raises(DomainError) as raised:
        route_message(db, message.id)
    assert raised.value.code == "incomplete_rfq"
    assert message.classification == "rfq"
    assert _routing(message)["rule"] == "customer_new_thread"


def test_reply_in_quoted_thread_records_model_decision(db):
    thread_id, session = _quoted_thread(db)
    reply = _reply(db, thread_id, "We accept your offer.")
    route_message(db, reply.id, judge=FixedJudge(ACCEPT))
    record = _routing(reply)
    assert reply.classification == "acceptance"
    assert record["decided_by"] == "gateway"
    assert record["model"] == "test-model"
    assert record["probabilities"]["A"] == 0.91
    db.refresh(session)
    assert session.stage == "booked"


def test_follow_up_in_quoted_thread_adds_one_timeline_event(db):
    thread_id, session = _quoted_thread(db)
    reply = _reply(db, thread_id, "Does the price include pickup?")
    route_message(db, reply.id, judge=FixedJudge(QUESTION))
    route_message(db, reply.id, judge=FixedJudge(QUESTION))
    events = [e for e in events_for(db, session.id) if e.type == "mail_routed"]
    assert len(events) == 1
    assert events[0].payload["kind"] == "follow_up"
    assert events[0].payload["external_id"] == reply.external_id
    db.refresh(session)
    assert session.stage == "quoted"


def test_mail_without_session_adds_no_event(db):
    message = _ingest(db, sender=AIRLINE)
    before = db.execute(select(func.count()).select_from(SessionRow)).scalar_one()
    route_message(db, message.id)
    after = db.execute(select(func.count()).select_from(SessionRow)).scalar_one()
    assert message.classification == "carrier_bid"
    assert before == after


def test_carrier_bid_job_finishes_done(db):
    message = _ingest(db, sender=AIRLINE)
    job = db.execute(
        select(JobRow).where(JobRow.payload["message_id"].astext == str(message.id))
    ).scalar_one()
    for _ in range(20):
        process_jobs_once(db, "test-worker")
        db.refresh(job)
        if job.status != "pending":
            break
    assert job.status == "done"
    assert message.classification == "carrier_bid"


def test_routing_same_rfq_twice_creates_one_rfq(db):
    message = _ingest(
        db,
        classification="rfq",
        raw={
            "rfq": {
                "origin": "FRA",
                "destination": "LAX",
                "weight_kg": "250",
                "volume_m3": "1.2",
                "pickup_required": True,
                "pickup_zone": "60",
                "service_intent": "cheapest",
            }
        },
    )
    route_message(db, message.id)
    route_message(db, message.id)
    count = db.execute(
        select(func.count()).select_from(RfqRow).where(RfqRow.message_id == message.id)
    ).scalar_one()
    assert count == 1
    assert _routing(message)["decided_by"] == "preset"


def test_rerouting_keeps_first_decision(db):
    thread_id, _ = _quoted_thread(db)
    reply = _reply(db, thread_id, "Does the price include pickup?")
    route_message(db, reply.id, judge=FixedJudge(QUESTION))
    route_message(db, reply.id, judge=FixedJudge(ACCEPT))
    assert reply.classification == "follow_up"


def test_unclear_reply_is_stored_as_unknown(db):
    from app.platform.decisions import UNCLEAR

    thread_id, session = _quoted_thread(db)
    reply = _reply(db, thread_id, "Thanks for the quote.")
    route_message(db, reply.id, judge=FixedJudge(UNCLEAR))
    assert reply.classification == "unknown"
    assert _routing(reply)["agent"] is None
    db.refresh(session)
    assert session.stage == "quoted"
