import logging

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
    if session.stage in {"accepted", "booked"}:
        return session
    session.stage = "accepted"
    rfq = db.get(RfqRow, session.rfq_id)
    if rfq is not None:
        rfq.status = "accepted"
    repositories.append_event(db, session.id, "customer_accepted", {"simulated": True})
    confirm_booking(db, session)
    return session


def confirm_booking(db: Session, session: SessionRow) -> BookingRow:
    existing = repositories.booking_for(db, session.rfq_id)
    if existing is not None:
        return existing
    quote = repositories.quote_for(db, session.rfq_id)
    if quote is None:
        raise DomainError("quote_required")
    candidate = db.get(RateCandidateRow, quote.candidate_id)
    rfq = db.get(RfqRow, session.rfq_id)
    if rfq is None:
        raise DomainError("rfq_required")
    booking = BookingRow(
        rfq_id=session.rfq_id,
        quote_id=quote.id,
        team_reference=f"TM-{session.id.hex[:10]}",
        rate_id=candidate.rate_id if candidate is not None else None,
        booking_reference=f"BK-SIM{session.id.hex[:5]}",
        status="confirmed",
        total=quote.total,
        currency=quote.currency,
        simulated=True,
    )
    db.add(booking)
    db.flush()
    intent = ActionIntentRow(
        rfq_id=rfq.id,
        action_key=f"book:{rfq.id}",
        kind="booking",
        payload={
            "simulated": True,
            "booking_reference": booking.booking_reference,
            "team_reference": booking.team_reference,
        },
        workflow_status="booked",
        delivery_status="confirmed",
        simulated=True,
    )
    db.add(intent)
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
        },
    )
    db.flush()
    return booking
