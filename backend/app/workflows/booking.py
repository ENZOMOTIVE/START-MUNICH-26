"""Booking agent: an acceptance of the stored quote becomes one booking.

The booking row is committed as `submitted` before the portal is called. `confirmed` is only
ever set from a portal answer or a carrier confirmation mail. An unclear portal outcome is
UNKNOWN and is reconciled by sending the identical (rate_id, team_reference) pair again, which
is the retry the booking contract defines. Emails never get that treatment.
"""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
from app.platform import get_adapters
from app.platform.booking import BookingRejected, BookingUnknown
from app.platform.carriers import load_carriers
from app.workflows import replies
from app.workflows import rfq as rfq_flow

log = logging.getLogger(__name__)
OPEN = ("pending", "submitted", "UNKNOWN")


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
    quote = repositories.quote_for(db, session.rfq_id)
    if quote is None:
        raise DomainError("quote_required")
    candidate = db.get(RateCandidateRow, quote.candidate_id)
    rfq = db.get(RfqRow, session.rfq_id)
    if rfq is None:
        raise DomainError("rfq_required")

    booking = repositories.booking_for(db, session.rfq_id)
    if booking is None:
        if candidate is None:
            raise DomainError("quoted_candidate_required")
        booking = BookingRow(
            rfq_id=session.rfq_id,
            quote_id=quote.id,
            team_reference=f"TM-{session.id.hex}",
            rate_id=candidate.rate_id,
            status="pending",
            total=quote.total,
            currency=quote.currency,
            simulated=True,
        )
        try:
            with db.begin_nested():
                db.add(booking)
                db.flush()
        except IntegrityError:
            # The stable team reference makes a concurrent replay land on the same row.
            booking = repositories.booking_for(db, session.rfq_id)
            if booking is None:
                raise

    if booking.status in OPEN and booking.rate_id:
        _submit(db, session, booking)
    elif booking.status in OPEN:
        _book_by_mail(db, session, rfq, booking, candidate)
    if booking.status == "confirmed":
        session.stage = "booked"
        rfq.status = "booked"
        if session.exception in {"booking_unknown", "send_unknown"}:
            session.exception = None
    _ensure_booking_intent(db, rfq.id, booking, quote.id)
    _notify(db, session, rfq, booking, candidate)
    db.flush()
    return booking


def _submit(db: Session, session: SessionRow, booking: BookingRow) -> None:
    adapter = get_adapters().get("booking")
    if adapter is None:
        # Demo mode has no portal: the booking is simulated and labelled as such.
        result = {
            "booking_reference": f"BK-SIM{session.id.hex}",
            "status": "confirmed",
            "simulated": True,
        }
    else:
        if db.scalar(select(SessionRow.paused).where(SessionRow.id == session.id)):
            raise DomainError("session_paused")
        if booking.status == "pending":
            booking.status = "submitted"
            repositories.append_event(
                db,
                session.id,
                "booking_submitted",
                {
                    "team_reference": booking.team_reference,
                    "rate_id": booking.rate_id,
                    "simulated": True,
                },
            )
            db.commit()  # durable before the external call
        try:
            result = adapter.book(booking.rate_id, booking.team_reference)
        except BookingUnknown as exc:
            booking.status = "UNKNOWN"
            session.exception = "booking_unknown"
            _booking_event(db, session, booking, "booking_unknown", str(exc))
            return
        except BookingRejected as exc:
            booking.status = "failed"
            session.exception = "booking_failed"
            session.stage = "exception"
            _booking_event(db, session, booking, "booking_failed", str(exc))
            return
    booking.booking_reference = result["booking_reference"]
    booking.status = result["status"]
    booking.simulated = bool(result.get("simulated")) or not get_settings().live_allowed()
    if booking.status == "confirmed":
        _booking_event(db, session, booking, "booking_confirmed", "portal")


def _book_by_mail(db, session, rfq: RfqRow, booking: BookingRow, candidate) -> None:
    """An offer with no portal rate is booked by writing to the airline. It stays `submitted`
    until the airline's "Booking <reference> confirmed" mail arrives."""
    airline = next(
        (
            c
            for c in load_carriers().values()
            if candidate is not None and c.kind == "airline" and c.name == candidate.carrier
        ),
        None,
    )
    if airline is None:
        booking.status = "failed"
        session.exception = "manual_booking_required"
        _booking_event(db, session, booking, "booking_failed", "no address for this carrier")
        return
    if db.scalar(select(SessionRow.paused).where(SessionRow.id == session.id)):
        raise DomainError("session_paused")
    mail = replies.carrier_booking_request(
        rfq.origin,
        rfq.destination,
        rfq_flow._weight(rfq.weight_kg),
        str(booking.total),
        booking.currency,
        booking.team_reference,
    )
    intent, created = rfq_flow.outbound(
        db, rfq, f"bookreq:{rfq.id}", "reply", None, mail, "accepted", to=airline.email
    )
    if created:
        booking.status = "submitted"
        _booking_event(db, session, booking, "booking_submitted", f"by mail to {airline.name}")
    try:
        rfq_flow.send(db, intent)
    except DomainError as exc:
        log.warning("booking request not sent for %s: %s", booking.team_reference, exc.code)


def _booking_event(db, session, booking: BookingRow, event: str, detail: str) -> None:
    repositories.append_event(
        db,
        session.id,
        event,
        {
            "simulated": bool(booking.simulated),
            "booking_reference": booking.booking_reference,
            "team_reference": booking.team_reference,
            "quote_decision_id": str(booking.quote_id),
            "rate_id": booking.rate_id,
            "total": str(booking.total),
            "currency": booking.currency,
            "status": booking.status,
            "detail": detail,
        },
    )


def _notify(db, session, rfq: RfqRow, booking: BookingRow, candidate) -> None:
    """Tell the customer the true booking state, once per state."""
    if booking.status not in {"confirmed", "submitted", "UNKNOWN", "failed"}:
        return
    root = db.get(MessageRow, rfq.message_id)
    name, carrier = rfq_flow.customer_name(root), candidate.carrier if candidate else "the carrier"
    if booking.status == "failed":
        state = "failed"
        events = repositories.events_for(db, session.id)
        failures = [e.payload["detail"] for e in events if e.type == "booking_failed"]
        detail = failures[-1] if failures else ""
        reason = "the carrier's booking system refused it"
        if "410" in detail:
            reason = "the rate expired before I could book it"
        mail = replies.booking_failed(name, root.subject, carrier, reason)
    elif booking.status == "confirmed":
        state = "confirmed"
        mail = replies.booking_confirmed(
            name,
            root.subject,
            carrier,
            booking.booking_reference,
            rfq.origin,
            rfq.destination,
            rfq_flow._weight(rfq.weight_kg),
            str(booking.total),
            booking.currency,
        )
    else:
        state = "submitted"
        mail = replies.booking_submitted(name, root.subject, carrier, booking.team_reference)
    intent, _ = rfq_flow.outbound(
        db,
        rfq,
        f"bookmail:{rfq.id}:{state}",
        "reply",
        rfq_flow.latest_customer_mail(db, root),
        mail,
        "booked" if state == "confirmed" else "accepted",
    )
    try:
        rfq_flow.send(db, intent)
    except DomainError as exc:  # the booking stands; the outbox has recorded the mail's state
        log.warning("booking mail not sent for %s: %s", booking.team_reference, exc.code)


def retry_open_bookings(db: Session) -> int:
    """Re-send the identical booking request for every booking whose outcome is not known."""
    rows = db.scalars(
        select(BookingRow).where(BookingRow.status.in_(("submitted", "UNKNOWN")))
    ).all()
    for booking in rows:
        session = repositories.session_by_rfq(db, booking.rfq_id)
        if session is not None and not session.paused:
            confirm_booking(db, session)
    return len(rows)


def _ensure_booking_intent(db: Session, rfq_id, booking: BookingRow, quote_id) -> ActionIntentRow:
    action_key = f"book:{rfq_id}"
    intent = repositories.action_intent_by_key(db, action_key)
    confirmed = booking.status == "confirmed"
    payload = {
        "simulated": bool(booking.simulated),
        "quote_id": str(quote_id),
        "rate_id": booking.rate_id,
        "booking_reference": booking.booking_reference,
        "team_reference": booking.team_reference,
        "status": booking.status,
    }
    workflow_status = "booked" if confirmed else "accepted"
    delivery_status = {"confirmed": "confirmed", "UNKNOWN": "UNKNOWN", "failed": "failed"}.get(
        booking.status, "sending"
    )
    if intent is None:
        intent = ActionIntentRow(
            rfq_id=rfq_id,
            action_key=action_key,
            kind="booking",
            payload=payload,
            workflow_status=workflow_status,
            delivery_status=delivery_status,
            simulated=bool(booking.simulated),
        )
        db.add(intent)
    else:
        intent.payload = payload
        intent.workflow_status = workflow_status
        intent.delivery_status = delivery_status
        intent.simulated = bool(booking.simulated)
    db.flush()
    return intent
