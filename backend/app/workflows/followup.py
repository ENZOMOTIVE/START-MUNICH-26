"""Customer replies, tracking updates and booking confirmations.

Tracking answers are built from stored facts only: the booking row and `tracking_update`
events. There is no shipment-status lookup, so a location is never invented.
"""

import hashlib
import logging
import re
from datetime import datetime
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db import repositories
from app.db.models import BookingRow, MessageRow, RateCandidateRow, RfqRow
from app.platform import get_adapters
from app.platform.carriers import carrier_for
from app.platform.decisions import reply_text
from app.workflows import booking as booking_flow
from app.workflows import replies
from app.workflows import rfq as rfq_flow

log = logging.getLogger(__name__)
_TRACKING_QUESTION = re.compile(
    r"\b(?:where|status|track\w*|eta|arriv\w*|deliver\w*|depart\w*|update)\b", re.IGNORECASE
)
_ENQUIRY_REF = re.compile(r"\[ref ([0-9a-f]{8}-[0-9a-f]{4})\]")
_BID_PRICE = re.compile(r"\b(EUR|USD)\s?(\d[\d,]*(?:\.\d{1,2})?)\b")
_BOOKING_REF = re.compile(r"\bBK-[A-Z0-9]{6,}\b")
_TEAM_REF = re.compile(r"\bTM-[0-9a-f]{32}\b")


def handle_follow_up(db: Session, message: MessageRow):
    session = repositories.session_by_thread(db, message.thread_id)
    if session is None:
        return None
    rfq = db.get(RfqRow, session.rfq_id)
    if session.stage == "clarifying":
        return rfq_flow.resume(db, session, rfq, message)
    booking = repositories.booking_for(db, rfq.id)
    text = reply_text(message.body) or message.subject
    if booking is not None and _TRACKING_QUESTION.search(text):
        return _answer_tracking(db, session, rfq, booking, message)
    return None  # nothing for this agent to do; the router notes the mail on the timeline


def _answer_tracking(db, session, rfq: RfqRow, booking: BookingRow, question: MessageRow):
    updates = [e for e in repositories.events_for(db, session.id) if e.type == "tracking_update"]
    latest = max(updates, key=lambda e: e.payload["reported_at"], default=None)
    quote = repositories.quote_for(db, rfq.id)
    candidate = db.get(RateCandidateRow, quote.candidate_id) if quote else None
    root = db.get(MessageRow, rfq.message_id)
    update = None
    if latest is not None:
        update = {
            "status": latest.payload["status"],
            "reported_at": datetime.fromisoformat(latest.payload["reported_at"]),
        }
    mail = replies.tracking_answer(
        rfq_flow.customer_name(root),
        question.subject or root.subject,
        booking.booking_reference,
        booking.status,
        candidate.carrier if candidate else "the carrier",
        update,
    )
    intent, created = rfq_flow.outbound(
        db,
        rfq,
        f"track:{question.external_id}",
        "tracking",
        question,
        mail,
        "booked" if booking.status == "confirmed" else "accepted",
    )
    if created:
        repositories.append_event(
            db,
            session.id,
            "tracking_answered",
            {
                "external_id": question.external_id,
                "evidence": {
                    "booking_reference": booking.booking_reference,
                    "booking_status": booking.status,
                    "tracking_update": latest.payload if latest else None,
                },
                "simulated": True,
            },
        )
    rfq_flow.send(db, intent)
    return session


def _booking_named_in(db: Session, message: MessageRow) -> BookingRow | None:
    text = f"{message.subject}\n{message.body}"
    for pattern, column in (
        (_BOOKING_REF, BookingRow.booking_reference),
        (_TEAM_REF, BookingRow.team_reference),
    ):
        match = pattern.search(text)
        if match:
            booking = db.scalars(select(BookingRow).where(column == match.group(0))).first()
            if booking is not None:
                return booking
    return None


def _unmatched(db: Session, message: MessageRow, kind: str) -> None:
    """A mail that names no booking we hold stays visible on the message, not dropped."""
    raw = dict(message.raw) if isinstance(message.raw, dict) else {}
    raw["unmatched"] = {"kind": kind, "reason": "no booking with a reference named in this mail"}
    message.raw = raw
    flag_modified(message, "raw")
    db.flush()
    log.warning("unmatched %s mail %s", kind, message.external_id)


def handle_tracking_update(db: Session, message: MessageRow):
    booking = _booking_named_in(db, message)
    if booking is None:
        return _unmatched(db, message, "tracking")
    session = repositories.session_by_rfq(db, booking.rfq_id)
    if rfq_flow.has_event(db, session.id, "tracking_update", message.external_id):
        return session  # the same update delivered twice is recorded once
    lines = [line.strip() for line in (message.body or "").splitlines() if line.strip()]
    repositories.append_event(
        db,
        session.id,
        "tracking_update",
        {
            "external_id": message.external_id,
            "status": (lines[0] if lines else message.subject)[:200],
            "reported_at": message.received_at.isoformat(),
            "booking_reference": booking.booking_reference,
            "source": message.sender,
            "simulated": bool(message.simulated),
        },
        occurred_at=message.received_at,
    )
    return session


def handle_carrier_bid(db: Session, message: MessageRow):
    """A trucker's answer to a pickup enquiry. Only its price and currency are read."""
    reference = _ENQUIRY_REF.search(message.subject or "")
    carrier = carrier_for(message.sender)
    price = _BID_PRICE.search(reply_text(message.body) or "")
    if not (reference and carrier and carrier.kind == "trucker" and price):
        return None
    rfq = db.scalars(
        select(RfqRow).where(cast(RfqRow.id, String).like(f"{reference.group(1)}%"))
    ).first()
    session = repositories.session_by_rfq(db, rfq.id) if rfq else None
    amount = Decimal(price.group(2).replace(",", ""))
    if session is None or not 0 < amount < 100000:
        return None
    if not rfq_flow.has_event(db, session.id, "carrier_bid", message.external_id):
        repositories.append_event(
            db,
            session.id,
            "carrier_bid",
            {
                "external_id": message.external_id,
                "kind": "pickup",
                "carrier": carrier.name,
                "amount": str(amount),
                "currency": price.group(1),
                "simulated": bool(message.simulated),
            },
            occurred_at=message.received_at,
        )
    # A bid that arrives after the quote is committed is recorded and changes nothing.
    if session.exception == rfq_flow.WAITING_FOR_PICKUP and rfq.status == "pending":
        root = db.get(MessageRow, rfq.message_id)
        found = rfq_flow.assess(db, root)
        if not found["missing"]:
            found["fields"] = rfq_flow._validated(found["fields"])
        rfq_flow.advance(db, session, rfq, root, found)
    return session


@lru_cache
def known_tariff_checksums() -> frozenset[str]:
    from app.pricing.tariff_candidates import TariffBook

    return frozenset(
        evidence.checksum
        for record in TariffBook.from_snapshot().records
        for evidence in record.evidence.values()
        if getattr(evidence, "checksum", None)
    )


def handle_tariff_mail(db: Session, message: MessageRow):
    """An airline's tariff mail: detect a file we have not approved and flag it for review.

    Nothing is ingested automatically; prices keep coming from the approved snapshot.
    """
    mailbox, files = get_adapters()["mailbox"], []
    for attachment in rfq_flow.attachments_of(message):
        entry = {"filename": attachment.get("filename"), "status": "unreadable"}
        try:
            content = mailbox.download_attachment(message.external_id, attachment.get("id"))
        except Exception as exc:  # noqa: BLE001 - any failure means the file was not read
            entry["detail"] = type(exc).__name__
        else:
            entry["sha256"] = hashlib.sha256(content).hexdigest()
            known = entry["sha256"] in known_tariff_checksums()
            entry["status"] = "unchanged" if known else "review_required"
        files.append(entry)
    raw = dict(message.raw) if isinstance(message.raw, dict) else {}
    changed = any(f["status"] != "unchanged" for f in files)
    raw["tariff"] = {"status": "review_required" if changed else "unchanged", "files": files}
    message.raw = raw
    flag_modified(message, "raw")
    db.flush()
    return None


def handle_booking_confirmation(db: Session, message: MessageRow):
    booking = _booking_named_in(db, message)
    if booking is None or carrier_for(message.sender) is None:
        return _unmatched(db, message, "booking_confirmation")
    session = repositories.session_by_rfq(db, booking.rfq_id)
    reference = _BOOKING_REF.search(f"{message.subject}\n{message.body}")
    if reference and not booking.booking_reference:
        booking.booking_reference = reference.group(0)  # a booking made by mail gets its id here
    if booking.status != "confirmed":
        booking.status = "confirmed"
        booking_flow._booking_event(
            db, session, booking, "booking_confirmed", f"carrier mail {message.external_id}"
        )
    booking_flow.confirm_booking(db, session)
    return session
