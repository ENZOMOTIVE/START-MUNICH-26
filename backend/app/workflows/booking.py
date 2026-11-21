import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db import repositories
from app.db.models import (
    ActionIntentRow,
    BookingRow,
    MessageRow,
    RateCandidateRow,
    RfqRow,
    SessionRow,
)

log = logging.getLogger(__name__)


def handle_acceptance(db: Session, message: MessageRow) -> SessionRow | None:
    session = repositories.session_by_thread(db, message.thread_id)
    if session is None:
        log.info("acceptance ignored; no session for thread %s", message.thread_id)
        return None

    rfq = db.get(RfqRow, session.rfq_id)
    if rfq is None:
        raise DomainError("rfq_required")
    quote = repositories.quote_for(db, session.rfq_id)
    if quote is None:
        raise DomainError("quote_required")

    existing_booking = repositories.booking_for(db, session.rfq_id)
    if existing_booking is None:
        candidate = db.get(RateCandidateRow, quote.candidate_id)
        if candidate is None:
            raise DomainError("quoted_candidate_required")
        if not candidate.rate_id:
            raise DomainError("rate_id_required")

    if session.stage not in {"accepted", "booked"} and existing_booking is None:
        session.stage = "accepted"
        rfq.status = "accepted"
        repositories.append_event(
            db,
            session.id,
            "customer_accepted",
            {
                "message_external_id": message.external_id,
                "quote_decision_id": str(quote.id),
                "simulated": True,
            },
        )
    confirm_booking(db, session)
    return session


def confirm_booking(db: Session, session: SessionRow) -> BookingRow:
    existing = repositories.booking_for(db, session.rfq_id)
    quote = repositories.quote_for(db, session.rfq_id)
    if quote is None:
        raise DomainError("quote_required")
    candidate = db.get(RateCandidateRow, quote.candidate_id)
    rfq = db.get(RfqRow, session.rfq_id)
    if rfq is None:
        raise DomainError("rfq_required")

    if existing is not None:
        session.stage = "booked"
        rfq.status = "booked"
        _ensure_booking_intent(db, rfq.id, existing, quote.id)
        return existing
    if candidate is None:
        raise DomainError("quoted_candidate_required")
    if not candidate.rate_id:
        raise DomainError("rate_id_required")

    booking = BookingRow(
        rfq_id=session.rfq_id,
        quote_id=quote.id,
        team_reference=f"TM-{session.id.hex}",
        rate_id=candidate.rate_id if candidate is not None else None,
        booking_reference=f"BK-SIM{session.id.hex}",
        status="confirmed",
        total=quote.total,
        currency=quote.currency,
        simulated=True,
    )
    try:
        with db.begin_nested():
            db.add(booking)
            db.flush()
            _ensure_booking_intent(db, rfq.id, booking, quote.id)
    except IntegrityError:
        # The stable team reference and action key make a concurrent replay safe.
        existing = repositories.booking_for(db, session.rfq_id)
        if existing is None:
            raise
        session.stage = "booked"
        rfq.status = "booked"
        _ensure_booking_intent(db, rfq.id, existing, quote.id)
        return existing

    session.stage = "booked"
    rfq.status = "booked"
    repositories.append_event(
        db,
        session.id,
        "booking_confirmed",
        {
            "simulated": True,
            "booking_reference": booking.booking_reference,
            "team_reference": booking.team_reference,
            "quote_decision_id": str(quote.id),
            "rate_id": booking.rate_id,
            "total": str(booking.total),
            "currency": booking.currency,
        },
    )
    db.flush()
    return booking


def _ensure_booking_intent(db: Session, rfq_id, booking: BookingRow, quote_id) -> ActionIntentRow:
    action_key = f"book:{rfq_id}"
    intent = repositories.action_intent_by_key(db, action_key)
    payload = {
        "simulated": True,
        "quote_id": str(quote_id),
        "rate_id": booking.rate_id,
        "booking_reference": booking.booking_reference,
        "team_reference": booking.team_reference,
        "status": booking.status,
    }
    if intent is None:
        intent = ActionIntentRow(
            rfq_id=rfq_id,
            action_key=action_key,
            kind="booking",
            payload=payload,
            workflow_status="booked",
            delivery_status="confirmed",
            simulated=True,
        )
        db.add(intent)
    else:
        intent.payload = payload
        intent.workflow_status = "booked"
        intent.delivery_status = "confirmed"
        intent.simulated = True
    db.flush()
    return intent
