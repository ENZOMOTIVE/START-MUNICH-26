from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db import repositories
from app.db.models import ActionIntentRow, MessageRow, QuoteDecisionRow, RfqRow, SessionRow
from app.platform.mailbox import FakeMailbox
from app.platform.outbox import deliver_intent
from app.pricing.calculator import select_offer
from app.pricing.tariffs import load_fixture_candidates

RFQ_TTL = timedelta(minutes=6)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_rfq_block(message: MessageRow) -> dict | None:
    raw = message.raw if isinstance(message.raw, dict) else {}
    block = raw.get("rfq")
    if not isinstance(block, dict):
        return None
    missing = not block.get("origin") or not block.get("destination")
    if missing or block.get("weight_kg") in (None, ""):
        return None
    return block


def handle_rfq(db: Session, message: MessageRow) -> SessionRow:
    block = _parse_rfq_block(message)
    if block is None:
        session = repositories.session_by_thread(db, message.thread_id)
        if session is not None and session.stage not in {"quoted", "accepted", "booked"}:
            session.exception = "incomplete_rfq"
            session.stage = "exception"
            repositories.append_event(
                db, session.id, "exception", {"code": "incomplete_rfq", "simulated": True}
            )
        raise DomainError("incomplete_rfq")

    rfq = repositories.rfq_by_message(db, message.id)
    existing_session = repositories.session_by_rfq(db, rfq.id) if rfq is not None else None
    if rfq is not None and existing_session is not None and rfq.status in {
        "quoted",
        "accepted",
        "booked",
    }:
        return existing_session
    if rfq is None:
        received_at = _aware(message.received_at)
        rfq = RfqRow(
            message_id=message.id,
            thread_id=message.thread_id,
            origin=str(block["origin"]),
            destination=str(block["destination"]),
            weight_kg=Decimal(str(block["weight_kg"])),
            volume_m3=(
                Decimal(str(block["volume_m3"]))
                if block.get("volume_m3") not in (None, "")
                else None
            ),
            pickup_required=bool(block.get("pickup_required", False)),
            pickup_zone=block.get("pickup_zone"),
            service_intent=str(block.get("service_intent") or "cheapest"),
            received_at=received_at,
            expires_at=received_at + RFQ_TTL,
            status="pending",
        )
        db.add(rfq)
        db.flush()

    session = repositories.session_by_rfq(db, rfq.id)
    if session is None:
        session = SessionRow(
            rfq_id=rfq.id,
            stage="intake",
            deadline=rfq.expires_at,
            simulated=True,
        )
        db.add(session)
        db.flush()

    repositories.append_event(
        db,
        session.id,
        "message_received",
        {"external_id": message.external_id, "simulated": True},
        simulated=True,
        occurred_at=message.received_at,
    )
    repositories.append_event(
        db,
        session.id,
        "rfq_extracted",
        {
            "origin": rfq.origin,
            "destination": rfq.destination,
            "weight_kg": str(rfq.weight_kg),
            "simulated": True,
        },
    )

    candidates = load_fixture_candidates(db, rfq)
    session.stage = "pricing"
    repositories.append_event(
        db,
        session.id,
        "rates_loaded",
        {"count": len(candidates), "simulated": True},
    )

    selected = select_offer(candidates, rfq.service_intent)
    repositories.append_event(
        db,
        session.id,
        "offer_selected",
        {
            "candidate_id": str(selected.id),
            "carrier": selected.carrier,
            "total": str(selected.total),
            "currency": selected.currency,
            "simulated": True,
        },
    )
    commit_first_quote(db, session, rfq, selected)
    return session


def commit_first_quote(db: Session, session: SessionRow, rfq: RfqRow, candidate):
    now = datetime.now(UTC)
    if session.paused:
        session.exception = "session_paused"
        repositories.append_event(
            db, session.id, "quote_blocked", {"reason": "session_paused", "simulated": True}
        )
        raise DomainError("session_paused")
    if now >= _aware(rfq.expires_at):
        rfq.status = "expired"
        session.exception = "expired"
        repositories.append_event(
            db, session.id, "quote_blocked", {"reason": "expired", "simulated": True}
        )
        raise DomainError("expired")

    existing_intent = repositories.first_quote_intent(db, rfq.id)
    if existing_intent is not None:
        return repositories.quote_for(db, rfq.id), existing_intent

    try:
        with db.begin_nested():
            decision = QuoteDecisionRow(
                rfq_id=rfq.id,
                candidate_id=candidate.id,
                total=candidate.total,
                currency=candidate.currency,
                decided_at=now,
                evidence={
                    "candidate_id": str(candidate.id),
                    "source_ref": candidate.source_ref,
                    "source_version": candidate.source_version,
                    "total": str(candidate.total),
                    "currency": candidate.currency,
                    "simulated": True,
                    "charges": candidate.charges or [],
                },
            )
            db.add(decision)
            db.flush()
            intent = ActionIntentRow(
                rfq_id=rfq.id,
                action_key=f"quote:{rfq.id}",
                kind="first_quote",
                payload={
                    "thread_id": rfq.thread_id,
                    "total": str(candidate.total),
                    "currency": candidate.currency,
                    "carrier": candidate.carrier,
                    "simulated": True,
                },
                workflow_status="quoted",
                delivery_status="pending",
                simulated=True,
            )
            db.add(intent)
            db.flush()
    except IntegrityError:
        decision = repositories.quote_for(db, rfq.id)
        intent = repositories.first_quote_intent(db, rfq.id)
        if intent is None:
            raise
        return decision, intent

    deliver_intent(db, intent, FakeMailbox())
    rfq.status = "quoted"
    session.stage = "quoted"
    repositories.append_event(db, session.id, "quote_recorded", {"simulated": True})
    return decision, intent
