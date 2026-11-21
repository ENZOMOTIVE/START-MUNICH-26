"""Customer replies, tracking updates and booking confirmations.

Tracking answers are built from stored facts only: the booking row and `tracking_update`
events. There is no shipment-status lookup, so a location is never invented.
"""

import asyncio
import hashlib
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from functools import lru_cache
from uuid import UUID

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db import repositories
from app.db.models import BookingRow, MessageRow, RateCandidateRow, RfqRow
from app.platform import get_adapters
from app.platform.carriers import carrier_for
from app.platform.decisions import reply_text
from app.platform.routing import ENQUIRY_REF as _ENQUIRY_REF
from app.platform.routing import sender_info
from app.workflows import booking as booking_flow
from app.workflows import documents, replies
from app.workflows import rfq as rfq_flow
from app.workflows.extract import _decimal, airports

log = logging.getLogger(__name__)
_TRACKING_QUESTION = re.compile(
    r"\b(?:where|status|track\w*|eta|arriv\w*|deliver\w*|depart\w*|update)\b", re.IGNORECASE
)
_AMOUNT = r"(\d[\d.,]*\d|\d)"  # as written; `_decimal` decides what the separators mean
_BID_PRICE = re.compile(rf"\b(EUR|USD)\s?{_AMOUNT}")
_PRICE_AFTER = re.compile(rf"\b{_AMOUNT}\s?(EUR|USD|€)")
_ROUTE = re.compile(r"\b([A-Z]{3})\s*(?:-|–|->|to)\s*([A-Z]{3})\b")
_BID_WEIGHT = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*kgs?\b", re.IGNORECASE)
# "including fuel" alone leaves the security charge open, so it is not all-in.
_ALL_IN = re.compile(
    r"\ball[- ]in\b|\ball\s+(?:sur)?charges\s+included\b"
    r"|\bincl(?:uding|usive\s+of|\.)?\s+(?:all\s+)?(?:surcharges|charges)\b"
    r"|\bincl(?:uding|usive\s+of|\.)?\s+fuel\s*(?:and|&|\+)\s*security\b",
    re.IGNORECASE,
)
_NOT_ALL_IN = re.compile(
    r"\bexcl|\bplus\s+(?:fuel|security|surcharges?)\b|\bper\s+kg\b|/\s?kg\b|\bsubject\s+to\b"
    r"|\bextra\b|\badditional(?:ly)?\b|\bon\s+top\b|\bnot\s+incl|\bsurcharges?\s+appl"
    r"|\+\s*(?:fuel|security|surcharges?)\b",
    re.IGNORECASE,
)
_VALID = re.compile(
    r"\bvalid\s+(?:until|till|through|to)\s+(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)",
    re.IGNORECASE,
)
_TRANSIT = re.compile(
    r"\btransit\D{0,12}(\d{1,2})\s*days?|\b(\d{1,2})\s*days?\s+transit", re.IGNORECASE
)
_BOOKING_REF = re.compile(r"\bBK-[A-Z0-9]{6,}\b")
_TEAM_REF = re.compile(r"\bTM-[0-9a-f]{32}\b")


def _prices(text: str) -> list[tuple[str, str]]:
    """(currency, number as written) of every price in the text: "EUR 245,50" or "245.50 EUR"."""
    after = _PRICE_AFTER.findall(_BID_PRICE.sub(" ", text))
    return _BID_PRICE.findall(text) + [(code.replace("€", "EUR"), raw) for raw, code in after]


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
    quote = repositories.quote_for(db, rfq.id)
    change = _changed_cargo(text, rfq) if quote is not None and booking is None else None
    if change and session.stage == "quoted":
        return _revised_quote(db, session, rfq, quote, message, *change)
    if booking is None and quote is not None and session.stage == "quoted" and "?" in text:
        return _answer_question(db, session, rfq, quote, message, text)
    return None  # nothing for this agent to do; the router notes the mail on the timeline


_CHANGE = re.compile(r"\b(chang\w*|updat\w*|revis\w*|correct\w*|instead)\b", re.IGNORECASE)
_VOLUME = re.compile(r"\b(\d+(?:[.,]\d+)?)\s*(?:cbm|m3|m³|cubic met)", re.IGNORECASE)


def _changed_cargo(text: str, rfq: RfqRow) -> tuple[Decimal, Decimal] | None:
    """New (weight, volume) when a reply to our quote changes the shipment. Live round 86
    ("the shipment details have changed to: 5 pieces, 1317 kg gross weight, 8.18 cbm") was
    scored "no answer": the board waits for the updated offer."""
    if not _CHANGE.search(text):
        return None
    weight = next((w for w in map(_decimal, _BID_WEIGHT.findall(text)) if w), None)
    volume = next((v for v in map(_decimal, _VOLUME.findall(text)) if v), None)
    new = (weight or Decimal(rfq.weight_kg), volume or Decimal(rfq.volume_m3 or 0))
    if (weight is None and volume is None) or new == (rfq.weight_kg, rfq.volume_m3):
        return None
    return new


def _revised_quote(db, session, rfq: RfqRow, quote, message: MessageRow, weight, volume):
    """Price the changed shipment and send the updated offer in the thread. The air leg is
    priced afresh; the stored decision moves to it, so a booking books the new rate.
    shortcut: the pickup is the bid we priced, scaled to the new chargeable weight (voice bids
    split about 35% base, 65% per kg) instead of asking the truckers again (about a minute)."""
    from app.pricing.candidates import build_rate_candidates
    from app.pricing.pickup import PickupQuote
    from app.pricing.tariffs import candidate_row
    from app.workflows.selection import choose_offer_with_evidence

    old = rfq_flow.pickup_bid(db, session, rfq) if rfq.pickup_required else None
    before = max(Decimal(rfq.weight_kg), Decimal(rfq.volume_m3 or 0) * 167)
    rfq.weight_kg, rfq.volume_m3 = weight, volume
    pickup = None
    if old is not None:
        ratio = max(Decimal(weight), Decimal(volume or 0) * 167) / before
        cost = (old.cost * (Decimal("0.35") + Decimal("0.65") * ratio)).quantize(Decimal("0.01"))
        evidence = old.evidence
        pickup = PickupQuote(old.zone, old.airport, cost, trucker=old.trucker, evidence=evidence)
    now = clock.now()
    rows = [candidate_row(c) for c in build_rate_candidates(rfq, pickup=pickup, now=now)]
    for row in rows:
        db.add(row)
    db.flush()
    selected, evidence = choose_offer_with_evidence(rows, rfq.service_intent, now=now)
    quote.candidate_id, quote.total, quote.currency = selected.id, selected.total, selected.currency
    quote.evidence = {
        **(evidence or {}),
        "revised_by": message.external_id,
        "calculation": rfq_flow._calculation(db, session, rfq, selected),
    }
    root = db.get(MessageRow, rfq.message_id)
    mail = replies.final_quote(
        rfq_flow.customer_name(root),
        root.subject,
        rfq.origin,
        rfq.destination,
        rfq_flow._weight(weight),
        selected.carrier,
        str(selected.total),
        selected.currency,
        selected.transit_days,
        [c.get("code") for c in selected.charges or [] if c.get("code") != "pickup"],
        selected.valid_until,
        rfq.pickup_zone if rfq.pickup_required else None,
        rfq_flow.pickup_reference(db, session),
    )
    key = f"requote:{rfq.id}:{message.external_id}"
    intent, created = rfq_flow.outbound(db, rfq, key, "reply", message, mail, "quoted")
    if created:
        repositories.append_event(
            db,
            session.id,
            "quote_revised",
            {"external_id": message.external_id, "total": str(selected.total), "simulated": True},
        )
    rfq_flow.send(db, intent)
    return session


def _answer_question(db, session, rfq: RfqRow, quote, question: MessageRow, text: str):
    """A question about the quote we sent ("does this include pickup?") is answered once,
    from the stored offer and without a price, so the thread keeps exactly one quote. A wish
    to change the price, route or service is not answered here: it goes to a person."""
    candidate = db.get(RateCandidateRow, quote.candidate_id)
    change = booking_flow.acceptance_problem(
        text, quote.total, quote.currency, rfq.origin, rfq.destination
    )
    if change and change != "conditional_or_deferred":
        return booking_flow._hold_acceptance(db, session, rfq, question, candidate.carrier, change)
    if any(e.type == "question_answered" for e in repositories.events_for(db, session.id)):
        return None  # the one answer already states everything the offer contains
    root = db.get(MessageRow, rfq.message_id)
    charges = [c.get("code") for c in candidate.charges or []]
    mail = replies.quote_answer(
        rfq_flow.customer_name(root),
        question.subject or root.subject,
        candidate.carrier,
        candidate.transit_days,
        [code for code in charges if code != "pickup"],
        candidate.valid_until,
        f"zone {rfq.pickup_zone}" if "pickup" in charges else None,
        rfq.origin,
    )
    intent, created = rfq_flow.outbound(
        db, rfq, f"answer:{rfq.id}", "reply", question, mail, "quoted"
    )
    if created:
        repositories.append_event(
            db,
            session.id,
            "question_answered",
            {"external_id": question.external_id, "charges": charges, "simulated": True},
        )
    rfq_flow.send(db, intent)
    return session


def _answer_tracking(db, session, rfq: RfqRow, booking: BookingRow, question: MessageRow):
    updates = [e for e in repositories.events_for(db, session.id) if e.type == "tracking_update"]
    latest = max(updates, key=lambda e: e.payload["reported_at"], default=None)
    root = db.get(MessageRow, rfq.message_id)
    # The live customer thanks us for every answer ("I look forward to the tracking update"),
    # which reads as a question again: say the same facts once to each asker, or they loop.
    for event in repositories.events_for(db, session.id):
        if (
            event.type == "tracking_answered"
            and event.payload["external_id"] != question.external_id  # a retry still sends
            and event.payload.get("sender", root.sender) == question.sender
            and event.payload["evidence"]["tracking_update"] == (latest.payload if latest else None)
        ):
            return session
    quote = repositories.quote_for(db, rfq.id)
    candidate = db.get(RateCandidateRow, quote.candidate_id) if quote else None
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
        f"{rfq_flow._weight(rfq.weight_kg)} kg from {rfq.origin} to {rfq.destination}",
        _ready_date(question.body, root.body),
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
                "sender": question.sender,
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


def _booking_described_in(db: Session, message: MessageRow) -> BookingRow | None:
    """tracking@ names the shipment, not our reference: "50978 to LIM, 704 kg" (mail 20755).
    Only an unambiguous match on destination and weight counts."""
    text = f"{message.subject}\n{message.body}"
    codes = set(re.findall(r"\b[A-Z]{3}\b", text))
    weights = {_decimal(w) for w in _BID_WEIGHT.findall(text)} - {None}
    found = [
        booking
        for booking in db.scalars(select(BookingRow)).all()
        if (rfq := db.get(RfqRow, booking.rfq_id)).destination in codes
        and rfq.weight_kg in weights
    ]
    return found[0] if len(found) == 1 else None


def _ready_date(*texts: str) -> str | None:
    """The shipment's ready date when it is still ahead: until then nothing was collected."""
    today = clock.now().date().isoformat()
    for text in texts:
        if (m := re.search(r"\b(20\d\d-\d\d-\d\d)\b", text or "")) and m[1] > today:
            return m[1]
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
    booking = _booking_named_in(db, message) or _booking_described_in(db, message)
    if booking is None:
        return _unmatched(db, message, "tracking")
    session = repositories.session_by_rfq(db, booking.rfq_id)
    if "?" in (reply_text(message.body) or message.subject or ""):
        # tracking@ asks on the customer's behalf (live mail 20755): answer it, it is no update.
        return _answer_tracking(db, session, db.get(RfqRow, booking.rfq_id), booking, message)
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


def read_rate_bid(text: str, rfq: RfqRow, now: datetime, direct_reply: bool) -> dict:
    """What an airline's or the rate desk's answer offers, and whether it can be used as it is.

    status: accepted | review | rejected. A reply for another shipment or with a lapsed
    validity is rejected; anything the reply leaves open is a person's decision, not a guess.
    """
    rejected, review = [], []
    known = airports()[1]
    routes = {pair for pair in _ROUTE.findall(text) if set(pair) <= known}
    if routes and (rfq.origin, rfq.destination) not in routes:
        rejected.append("it is for another route")
    elif not routes and not direct_reply:
        review.append("it does not restate the route and is not a direct reply to our enquiry")
    weights = [Decimal(w.replace(",", ".")) for w in _BID_WEIGHT.findall(text)]
    if weights and all(abs(w - Decimal(rfq.weight_kg)) > Decimal("0.5") for w in weights):
        review.append("it names a weight other than the one requested")
    prices = sorted(set(_prices(text)))
    amount = _decimal(prices[0][1]) if len(prices) == 1 else None
    if len(prices) == 1 and amount is None:
        review.append(f"the amount '{prices[0][1]}' can be read in two ways")
    elif amount is None or not 0 < amount < 1000000:
        review.append("it does not state exactly one price")
    elif prices[0][0] != "EUR":
        review.append(f"the price is in {prices[0][0]}")
    if _NOT_ALL_IN.search(text):
        review.append("charges come on top of the stated price")
    elif not _ALL_IN.search(text):
        review.append("it does not say that all charges are included")
    valid_until, stated = None, _VALID.search(text)
    if stated:
        # A date alone is good until the end of that day.
        stamp = stated.group(1).replace(" ", "T")
        valid_until = datetime.fromisoformat(stamp if "T" in stamp else f"{stamp}T23:59")
        valid_until = valid_until.replace(tzinfo=UTC)
        if valid_until <= now:
            rejected.append("its validity has passed")
    transit = _TRANSIT.search(text)
    return {
        "status": "rejected" if rejected else "review" if review else "accepted",
        "reasons": rejected + review,
        "amount": str(amount) if amount is not None else None,
        "currency": prices[0][0] if len(prices) == 1 else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "transit_days": int(transit.group(1) or transit.group(2)) if transit else None,
    }


def _rate_bid(db: Session, session, rfq: RfqRow, message: MessageRow, source: dict) -> dict:
    """Check an airline's or the rate desk's reply against the request it answers."""
    enquiry = repositories.action_intent_by_key(
        db, f"rate-enquiry:{rfq.id}:{source['email']}"[:128]
    )
    mail = (message.raw or {}).get("mail") or {}
    sent_id = ((enquiry.payload or {}).get("_send") or {}).get("id") if enquiry else None
    text = reply_text(message.body) or ""
    direct = sent_id is not None and str(mail.get("in_reply_to")) == str(sent_id)
    bid = read_rate_bid(text, rfq, clock.now(), direct)
    carrier = source["name"]
    if source["kind"] == "rate_desk":
        # The rate desk answers for an airline; the booking has to go to that airline.
        listed = rfq_flow.airlines_for(rfq.destination)
        carrier = next((name for name, _ in listed if name.lower() in text.lower()), None)
        if carrier is None and bid["status"] != "rejected":
            bid["status"] = "review"
            bid["reasons"].append("it names no listed airline that could be booked")
    if enquiry is None or enquiry.delivery_status != "confirmed":
        bid["status"] = "rejected"
        bid["reasons"].insert(0, "we did not ask this sender about this request")
    if bid["status"] == "accepted" and rfq.pickup_required:
        if rfq_flow.pickup_bid(db, session, rfq) is None:
            bid["status"] = "review"
            bid["reasons"].append("the request needs a pickup and no trucker price is on file")
    return {**bid, "kind": "rate", "carrier": carrier or "Rate desk", "rfq_id": str(rfq.id)}


def use_rate_bid(db: Session, session, rfq: RfqRow, message: MessageRow, bid: dict) -> None:
    """Turn a usable bid into an offer and go on pricing. Safe to call twice for one mail."""
    source_ref = f"mail:{message.external_id}"
    if bid.get("amount") is None or bid.get("currency") != "EUR":
        raise DomainError("bid_has_no_usable_price")
    if any(c.source_ref == source_ref for c in repositories.candidates_for(db, rfq.id)):
        return
    pickup = rfq_flow.pickup_bid(db, session, rfq) if rfq.pickup_required else None
    if rfq.pickup_required and pickup is None:
        raise DomainError("pickup_price_missing")
    charges = [{"code": "air_freight_all_in", "amount": bid["amount"], "currency": "EUR"}]
    if pickup is not None:
        charges.append({"code": "pickup", "amount": str(pickup.cost), "currency": "EUR"})
    valid_until = bid.get("valid_until")
    db.add(
        RateCandidateRow(
            rfq_id=rfq.id,
            carrier=bid["carrier"],
            total=sum(Decimal(c["amount"]) for c in charges),
            currency="EUR",
            transit_days=bid.get("transit_days"),
            rate_id=None,  # no portal rate: booked by writing to the airline
            complete=True,
            valid_until=datetime.fromisoformat(valid_until) if valid_until else None,
            source_ref=source_ref,
            source_version="email-bid",
            charges=charges,
            simulated=bool(message.simulated) or not get_settings().live_allowed(),
        )
    )
    db.flush()
    if session.exception == rfq_flow.WAITING_FOR_RATE and rfq.status == "pending":
        _continue_pricing(db, session, rfq)


def _continue_pricing(db: Session, session, rfq: RfqRow) -> None:
    root = db.get(MessageRow, rfq.message_id)
    found = rfq_flow.assess(db, root)
    if not found["missing"]:
        found["fields"] = rfq_flow._validated(found["fields"])
    rfq_flow.advance(db, session, rfq, root, found)


def _flag(db: Session, message: MessageRow, key: str, value: dict) -> None:
    message.raw = {**(message.raw if isinstance(message.raw, dict) else {}), key: value}
    flag_modified(message, "raw")
    db.flush()


def handle_carrier_bid(db: Session, message: MessageRow):
    """A carrier's answer to one of our enquiries: a trucker's pickup price, or a rate from an
    airline or the rate desk. The `[ref ...]` we put in the subject says which request."""
    reference = _ENQUIRY_REF.search(message.subject or "")
    source = sender_info(message.sender)
    rfq = (
        db.scalars(
            select(RfqRow).where(cast(RfqRow.id, String).like(f"{reference.group(1)}%"))
        ).first()
        if reference
        else None
    )
    session = repositories.session_by_rfq(db, rfq.id) if rfq else None
    if session is None:
        if source["kind"] == "rate_desk" and not (message.raw or {}).get("unmatched"):
            reason = "no enquiry of ours matches this reply"
            _flag(db, message, "unmatched", {"kind": "rate_desk_reply", "reason": reason})
        return None
    if source["kind"] != "trucker":
        bid = _rate_bid(db, session, rfq, message, source)
    else:
        # Truckers also send their offer as a file (an offer letter, a voice message).
        files = documents.collect(db, message)
        text = "\n".join([reply_text(message.body) or "", *[f.get("text", "") for f in files]])
        # An offer letter lists its parts next to the total ("base 46.00 EUR, weight charge
        # 67.13 EUR"): the total is the largest amount. One unclear amount holds the bid.
        prices = sorted(_prices(text), key=lambda p: _decimal(p[1]) or Decimal("Infinity"))[::-1]
        amount = _decimal(prices[0][1]) if prices else None
        if prices and amount is None:
            # "1.200 EUR" is 1200 or 1.20: never priced on a guess, kept for a person.
            reason = f"the amount '{prices[0][1]}' can be read in two ways"
            bid = {"kind": "pickup_unclear", "carrier": source["name"], "text": text[:300]}
            if not (message.raw or {}).get("unmatched"):
                _flag(db, message, "unmatched", {"kind": "pickup_bid", "reason": reason})
        elif amount is None or not 0 < amount < 100000:
            # A refusal ("we do not serve this zone") is an answer too: it is recorded, so
            # the request does not wait for this trucker any longer. It never prices.
            unread = any(not f["readable"] for f in files)  # an offer we could not open
            kind = "pickup_unreadable" if unread else "pickup_declined"
            bid = {"kind": kind, "carrier": source["name"], "text": text[:300]}
        else:
            hours = rfq_flow.PICKUP_HOURS.search(text)  # the full text: voice mails run long
            bid = {
                "kind": "pickup",
                "carrier": source["name"],
                "amount": str(amount),
                "currency": prices[0][0],
                "hours": int(hours[1]) if hours else None,
                # "give our offer number in your quotation"
                "reference": (re.search(r"\bTRK-\d+\b", text) or [None])[0],
                "text": text[:300],
            }
    if not rfq_flow.has_event(db, session.id, "carrier_bid", message.external_id):
        repositories.append_event(
            db,
            session.id,
            "carrier_bid",
            {"external_id": message.external_id, **bid, "simulated": bool(message.simulated)},
            occurred_at=message.received_at,
        )
    if bid["kind"] == "rate":
        if "bid" not in (message.raw or {}):
            status = "review_required" if bid["status"] == "review" else bid["status"]
            _flag(db, message, "bid", {**bid, "status": status})
        if bid["status"] == "accepted":
            use_rate_bid(db, session, rfq, message, bid)
    # A pickup bid that arrives after the quote is committed is recorded and changes nothing.
    elif session.exception == rfq_flow.WAITING_FOR_PICKUP and rfq.status == "pending":
        if rfq_flow.pickup_bid(db, session, rfq) and rfq_flow.pickup_answers_in(db, session, rfq):
            _continue_pricing(db, session, rfq)
    return session


def refresh_tariff_offers(db: Session, carrier: str) -> int:
    """After a tariff review: reprice this carrier's tariff offers for requests that are still
    open, from the approved snapshot. Rows are updated in place; a quoted request keeps its
    offer. Returns how many requests were repriced."""
    from app.pricing.candidates import _price_request
    from app.pricing.contract import option_to_contract
    from app.pricing.tariff_candidates import TariffBook, build_candidates

    known_tariff_checksums.cache_clear()
    book, now, repriced = TariffBook.from_snapshot(), clock.now(), 0
    rows = db.scalars(
        select(RateCandidateRow)
        .join(RfqRow, RfqRow.id == RateCandidateRow.rfq_id)
        .where(
            RateCandidateRow.carrier == carrier,
            RateCandidateRow.rate_id.is_(None),
            RateCandidateRow.source_version != "email-bid",
            RfqRow.status == "pending",
        )
    ).all()
    for row in rows:
        rfq = db.get(RfqRow, row.rfq_id)
        session = repositories.session_by_rfq(db, rfq.id)
        if session is None or repositories.quote_for(db, rfq.id) is not None:
            continue
        pickup = rfq_flow.pickup_bid(db, session, rfq) if rfq.pickup_required else None
        priced = asyncio.run(
            build_candidates(_price_request(rfq), book, None, pickup=pickup, now=now)
        )
        offers = [option_to_contract(option, rfq.id, now=now) for option in priced.candidates]
        fresh = next((offer for offer in offers if offer.carrier == carrier), None)
        if fresh is None:
            row.complete = False  # the approved tariff no longer offers this lane
        else:
            for name in ("total", "currency", "transit_days", "complete", "valid_until"):
                setattr(row, name, getattr(fresh, name))
            row.source_ref, row.source_version = fresh.source_ref, fresh.source_version
            row.charges = [charge.model_dump(mode="json") for charge in fresh.charges]
        db.flush()
        repriced += 1
        if session.exception == rfq_flow.WAITING_FOR_RATE:
            _continue_pricing(db, session, rfq)
    return repriced


def apply_review(db: Session, message: MessageRow, approve: bool = False) -> dict:
    """A person has dealt with a flagged mail. Approving a bid prices with it; clearing a
    tariff hold reprices that carrier's open offers."""
    raw, result = dict(message.raw or {}), {}
    bid = raw.get("bid") if (raw.get("bid") or {}).get("status") == "review_required" else None
    if bid and approve:
        rfq = db.get(RfqRow, UUID(bid["rfq_id"]))
        use_rate_bid(db, repositories.session_by_rfq(db, rfq.id), rfq, message, bid)
        result["bid_used"] = True
        raw = dict(message.raw or {})
    if bid:
        raw["bid"] = {**bid, "status": "approved" if approve else "dismissed"}
    held = (raw.get("tariff") or {}).get("status") == "review_required"
    if "tariff" in raw:
        raw["tariff"] = {**raw["tariff"], "status": "reviewed"}
    raw.pop("unmatched", None)
    message.raw = raw
    flag_modified(message, "raw")
    db.flush()
    carrier = carrier_for(message.sender)
    if held and carrier is not None:
        result["repriced_requests"] = refresh_tariff_offers(db, carrier.name)
    return result


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
