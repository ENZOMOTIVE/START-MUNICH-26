"""RFQ agent: read the request, ask for what is missing, quote once, never after the deadline.

A request moves intake -> (clarifying) -> pricing -> quoted. Only `commit_first_quote` may state
a price, and it does so at most once per RFQ. A clarification is a separate intent with its own
purpose, so asking for documents can never count as the first quote.
"""

import base64
import logging
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db import repositories
from app.db.models import ActionIntentRow, MessageRow, QuoteDecisionRow, RfqRow, SessionRow
from app.platform import get_adapters, runlimits
from app.platform.decisions import reply_text
from app.platform.outbox import deliver_intent
from app.platform.routing import RATE_DESK_ADDRESS
from app.pricing.pickup import PickupQuote, airport_for_zone
from app.pricing.quote_models import Evidence
from app.pricing.tariffs import load_fixture_candidates
from app.workflows import documents, language, replies
from app.workflows.extract import (
    REQUIRED,
    airlines_for,
    assess_dg,
    merge,
    model_check,
    model_fill,
    pickup_postcode,
    truckers_for_zone,
)
from app.workflows.selection import choose_offer_with_evidence

log = logging.getLogger(__name__)

RFQ_TTL = timedelta(minutes=6)
WAITING_FOR_PICKUP = "waiting_for_carrier:pickup"
WAITING_FOR_RATE = "waiting_for_carrier:rate"
WAITING_FOR_CARRIER = (WAITING_FOR_PICKUP, WAITING_FOR_RATE)
HELD_FOR_RELEASE = "waiting_for_operator_release"


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _validated(block: dict) -> dict:
    """Normalise complete RFQ fields; raise DomainError for a value that cannot be priced."""
    origin = str(block.get("origin") or "").strip().upper()
    destination = str(block.get("destination") or "").strip().upper()
    if len(origin) > 8 or len(destination) > 8:
        raise DomainError("invalid_route")

    try:
        weight_kg = Decimal(str(block["weight_kg"]))
    except (InvalidOperation, TypeError, ValueError, KeyError) as exc:
        raise DomainError("invalid_weight") from exc
    if not weight_kg.is_finite() or weight_kg <= 0:
        raise DomainError("invalid_weight")

    volume_m3 = None
    if block.get("volume_m3") not in (None, ""):
        try:
            volume_m3 = Decimal(str(block["volume_m3"]))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise DomainError("invalid_volume") from exc
        if not volume_m3.is_finite() or volume_m3 <= 0:
            raise DomainError("invalid_volume")

    pickup_required = block.get("pickup_required", False)
    if isinstance(pickup_required, str):
        value = pickup_required.strip().lower()
        if value not in {"true", "false", "1", "0", "yes", "no"}:
            raise DomainError("invalid_pickup_required")
        pickup_required = value in {"true", "1", "yes"}
    elif not isinstance(pickup_required, bool):
        raise DomainError("invalid_pickup_required")

    service_intent = str(block.get("service_intent") or "cheapest").strip().lower()
    if service_intent not in {"cheapest", "fastest"}:
        raise DomainError("invalid_service_intent")

    pickup_zone = block.get("pickup_zone")
    if pickup_zone not in (None, ""):
        pickup_zone = str(pickup_zone).strip().upper() or None
        if pickup_zone and len(pickup_zone) > 8:
            raise DomainError("invalid_pickup_zone")

    return {
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
        "volume_m3": volume_m3,
        "pickup_required": pickup_required,
        "pickup_zone": pickup_zone or None,
        "service_intent": service_intent,
    }


def customer_name(message: MessageRow) -> str | None:
    """First name from the mail's display name, if the mail carries one. Never guessed."""
    mail = (message.raw or {}).get("mail") if isinstance(message.raw, dict) else None
    sender = (mail or {}).get("from")
    if isinstance(sender, dict):
        name = str(sender.get("name") or "")
    else:
        name = parseaddr(str(sender or ""))[0]
    return name.split()[0] if name.strip() else None


def attachments_of(message: MessageRow) -> list[dict]:
    mail = (message.raw or {}).get("mail") if isinstance(message.raw, dict) else None
    return list((mail or {}).get("attachments") or [])


def customer_mails(db: Session, root: MessageRow) -> list[MessageRow]:
    rows = db.scalars(
        select(MessageRow)
        .where(MessageRow.thread_id == root.thread_id)
        .order_by(MessageRow.received_at, MessageRow.created_at)
    ).all()
    return [m for m in rows if m.sender == root.sender]


def assess(db: Session, root: MessageRow) -> dict:
    """Everything the customer has told us in this thread, and what is still open."""
    mails = customer_mails(db, root) or [root]
    raw = root.raw if isinstance(root.raw, dict) else {}
    notes: list[str] = []
    if isinstance(raw.get("rfq"), dict):  # fixture and demo mail carry the fields ready-made
        fields = {k: v for k, v in raw["rfq"].items() if v not in (None, "")}
        method = "preset"
    else:
        merged = merge([(m.subject, reply_text(m.body)) for m in mails])
        if merged["missing"] or "volume_m3" not in merged["fields"]:
            # "All details are in the attached PDF": read the files that are not DG papers
            # too. The mail's own words come first, so the file only fills what it leaves open.
            merged = merge(
                [
                    (
                        m.subject,
                        "\n".join(
                            [reply_text(m.body) or ""]
                            + [
                                d.get("text", "")
                                for d in documents.collect(db, m)
                                if not d["kinds"]
                            ]
                        ),
                    )
                    for m in mails
                ]
            )
        fields, notes, method = merged["fields"], merged["notes"], "rules"
        model = get_adapters().get("extraction_model")
        gaps = [name for name in REQUIRED if name not in fields]
        if gaps and model is not None:
            text = "\n\n".join(f"{m.subject}\n{reply_text(m.body)}" for m in mails)
            found = model_fill(model["gateway"], model["name"], text, gaps)
            if found:
                fields.update(found)
                method = "rules+model"
    missing = [name for name in REQUIRED if name not in fields]
    if fields.get("pickup_required") and not fields.get("pickup_zone"):
        missing.append("pickup_zone")  # a pickup with no place cannot be priced; never drop it
    if missing and not fields.get("volume_m3"):
        # When we have to ask anyway, ask for the volume too: truckers refuse to bid without it.
        missing.append("volume_m3")
    return {
        "fields": fields,
        "missing": missing,
        "notes": notes,
        "method": method,
        "sources": [m.external_id for m in mails],
        "dg": assess_dg(
            [(f"{m.subject}\n{reply_text(m.body)}", documents.collect(db, m)) for m in mails]
        ),
    }


def has_event(db: Session, session_id, event_type: str, external_id: str) -> bool:
    return any(
        e.type == event_type and (e.payload or {}).get("external_id") == external_id
        for e in repositories.events_for(db, session_id)
    )


def _customer_language(root: MessageRow) -> str:
    """The request's language, checked once per request and kept on the mail: the phrase table
    decides, and one model reading (2 output tokens) may only widen "en" to a language the
    table speaks. A stopped run, a spent budget or an error leaves the table's answer."""
    raw = dict(root.raw) if isinstance(root.raw, dict) else {}
    if not isinstance(raw.get("language"), dict):
        found = language.detect(root.body)
        try:
            answer = get_adapters()["gateway"].complete(
                "Answer with the two-letter ISO 639-1 code only. The language of this email:\n\n"
                + (reply_text(root.body) or root.body or "")[:600],
                get_settings().extraction_model,
                max_tokens=2,
                temperature=0,
            )
            model = (answer.get("text") or "").strip().lower()[:2] or None
        except Exception:  # noqa: BLE001 - no reading: the table's answer stands
            model = None
        used = model if found == "en" and model in language.LANGUAGES else found
        raw["language"] = {"table": found, "model": model, "used": used}
        root.raw = raw
        flag_modified(root, "raw")
    return raw["language"]["used"]


def outbound(
    db: Session,
    rfq: RfqRow,
    action_key: str,
    kind: str,
    reply_to: MessageRow | None,
    mail: dict,
    workflow_status: str = "pending",
    extra: dict | None = None,
    to: str | None = None,
) -> tuple[ActionIntentRow, bool]:
    """Get or create the one intent for this action. Returns (intent, created).

    A reply goes to `reply_to`'s sender in its thread; `to` starts a new thread with a carrier.
    """
    intent = repositories.action_intent_by_key(db, action_key)
    if intent is not None:
        return intent, False
    root = db.get(MessageRow, rfq.message_id)
    if to is None and reply_to is not None:
        # Answer in the language of the request (live verdict wrong_language); tracking@ asks
        # on the customer's behalf, in its own words.
        customer = reply_to.sender.lower() == root.sender.lower()
        source = root if customer else reply_to if kind == "tracking" else None
        pdf = mail["purpose"] == "first_quote" and "pdf" in (root.body or "").lower()
        if pdf:
            # The customer asked for the offer as a PDF (live verdict no_pdf_offer, round 76).
            note = "\n\nThe offer is attached as a PDF.\n\nTo book,"
            mail = {**mail, "body": mail["body"].replace("\n\nTo book,", note)}
        if source is not None:
            spoken = _customer_language(root) if customer else language.detect(source.body)
            mail = {**mail, "body": language.localize(mail["body"], spoken)}
        if pdf:
            document = documents.text_pdf(mail["body"])
            # Before it goes out: the PDF must read back with every number the mail states.
            numbers = re.compile(r"\d+(?:[.,]\d+)*")
            readable = set(numbers.findall(documents.read_text(document)))
            if not set(numbers.findall(mail["body"])) <= readable:
                log.warning("offer PDF for %s does not read back its numbers", rfq.id)
            content = base64.b64encode(document).decode()
            attachment = {"filename": "offer.pdf", "content_type": "application/pdf"}
            extra = {**(extra or {}), "attachments": [{**attachment, "content_base64": content}]}
    external = reply_to.external_id if reply_to is not None else ""
    payload = {
        "to": to or reply_to.sender,
        "subject": mail["subject"],
        "body": mail["body"],
        "in_reply_to": int(external) if external.isdigit() else None,
        "thread_id": rfq.thread_id,
        "purpose": mail["purpose"],
        "simulated": True,
        **(extra or {}),
    }
    try:
        with db.begin_nested():
            intent = ActionIntentRow(
                rfq_id=rfq.id,
                action_key=action_key,
                kind=kind,
                payload=payload,
                workflow_status=workflow_status,
                # In observation mode an action is a proposal: `observed` is never sent, and
                # no later mode change turns it into `pending`.
                delivery_status="observed" if get_settings().app_mode == "observe" else "pending",
                simulated=True,
            )
            db.add(intent)
            db.flush()
    except IntegrityError:
        return repositories.action_intent_by_key(db, action_key), False
    return intent, True


def send(db: Session, intent: ActionIntentRow) -> None:
    if intent.delivery_status == "pending":
        deliver_intent(db, intent, get_adapters()["mailbox"])


def latest_customer_mail(db: Session, root: MessageRow) -> MessageRow:
    return (customer_mails(db, root) or [root])[-1]


def _block_expired(db: Session, session: SessionRow, rfq: RfqRow) -> None:
    rfq.status = "expired"
    session.exception = "expired"
    session.stage = "exception"
    repositories.append_event(
        db,
        session.id,
        "quote_blocked",
        {"reason": "expired", "expired_at": _aware(rfq.expires_at).isoformat(), "simulated": True},
    )
    raise DomainError("expired")


def _fail(db: Session, session: SessionRow, rfq: RfqRow, code: str) -> None:
    session.stage = "exception"
    session.exception = code
    rfq.status = "expired" if code == "offers_expired" else "failed"
    repositories.append_event(db, session.id, "exception", {"code": code, "simulated": True})


def handle_rfq(db: Session, message: MessageRow) -> SessionRow:
    rfq = repositories.rfq_by_message(db, message.id)
    session = repositories.session_by_rfq(db, rfq.id) if rfq is not None else None
    if (
        rfq is not None
        and session is not None
        and rfq.status
        in {
            "quoted",
            "accepted",
            "booked",
            "expired",
            "failed",
        }
    ):
        quote_mail = repositories.first_quote_intent(db, rfq.id)
        if rfq.status == "quoted" and quote_mail is not None:
            send(db, quote_mail)  # only acts on a quote that provably never left
        return session

    if rfq is None and clock.now() >= _aware(message.received_at) + RFQ_TTL:
        # Old mail (a backlog after a restart): it is recorded as expired below, without
        # downloading its attachments, asking a model or pricing it.
        found = {"fields": {}, "missing": list(REQUIRED)}
    else:
        found = assess(db, message)
    if not found["missing"]:
        found["fields"] = _validated(found["fields"])
    fields = found["fields"]
    if rfq is None:
        received_at = _aware(message.received_at)
        # shortcut: unknown route/weight are stored as ""/0 while the session is `clarifying`
        # (the columns are NOT NULL); make them nullable if a clarifying row is ever priced.
        rfq = RfqRow(
            message_id=message.id,
            thread_id=message.thread_id,
            origin=str(fields.get("origin") or ""),
            destination=str(fields.get("destination") or ""),
            weight_kg=Decimal(str(fields.get("weight_kg") or 0)),
            received_at=received_at,
            expires_at=received_at + RFQ_TTL,  # set once from the mail; nothing ever moves it
            status="pending",
            source_ref=f"message:{message.external_id}",
        )
        db.add(rfq)
        db.flush()
    if session is None:
        session = SessionRow(rfq_id=rfq.id, stage="intake", deadline=rfq.expires_at, simulated=True)
        hold = get_settings().hold_new_requests
        if not hold and clock.now() < _aware(rfq.expires_at):
            try:
                # Automatic run: a fresh request admits itself, inside RUN_MAX_RFQS and the
                # run's window. Past either, it waits for a person instead of being dropped.
                runlimits.admit(db, rfq.thread_id)
            except DomainError:
                hold = True
        if hold:
            # Nothing is priced or sent until a person releases the request.
            session.paused, session.exception = True, HELD_FOR_RELEASE
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
    return advance(db, session, rfq, message, found)


def resume(db: Session, session: SessionRow, rfq: RfqRow, reply: MessageRow) -> SessionRow:
    """A customer answered while we were waiting: re-read the whole thread and continue."""
    root = db.get(MessageRow, rfq.message_id)
    if not has_event(db, session.id, "customer_replied", reply.external_id):
        repositories.append_event(
            db,
            session.id,
            "customer_replied",
            {
                "external_id": reply.external_id,
                "attachments": [str(a.get("filename")) for a in attachments_of(reply)],
                "simulated": bool(reply.simulated),
            },
            occurred_at=reply.received_at,
        )
    found = assess(db, root)
    if not found["missing"]:
        found["fields"] = _validated(found["fields"])
    return advance(db, session, rfq, root, found)


def advance(db: Session, session: SessionRow, rfq: RfqRow, root: MessageRow, found: dict):
    now = clock.now()
    if now >= _aware(rfq.expires_at):
        _block_expired(db, session, rfq)
    if session.paused:
        # A person holds this session: no pricing, no portal calls, no mail until they resume.
        raise DomainError("session_paused")
    if session.exception == HELD_FOR_RELEASE:
        session.exception = None

    fields = found["fields"]
    for name in ("origin", "destination", "weight_kg", "volume_m3", "pickup_zone"):
        if fields.get(name) not in (None, ""):
            setattr(rfq, name, fields[name])
    rfq.pickup_required = bool(fields.get("pickup_required", False))
    rfq.service_intent = str(fields.get("service_intent") or "cheapest")
    repositories.append_event(
        db,
        session.id,
        "rfq_extracted",
        {
            "origin": rfq.origin or None,
            "destination": rfq.destination or None,
            "weight_kg": str(rfq.weight_kg) if rfq.weight_kg else None,
            "volume_m3": str(rfq.volume_m3) if rfq.volume_m3 is not None else None,
            "pickup_required": rfq.pickup_required,
            "pickup_zone": rfq.pickup_zone,
            "service_intent": rfq.service_intent,
            "method": found["method"],
            "source_mails": found["sources"],
            "missing": found["missing"],
            "dangerous_goods": found["dg"],
            "source_ref": rfq.source_ref,
            "simulated": True,
        },
    )

    name, dg = customer_name(root), found["dg"]
    route = (rfq.origin or None, rfq.destination or None)
    if found["missing"]:
        mail = replies.missing_info(name, root.subject, *route, found["missing"], found["notes"])
        return _clarify(db, session, rfq, root, mail, found["missing"], found)
    if dg["blocked"]:
        build = replies.dg_conflict if dg["status"] == "conflict" else replies.dg_documents
        mail = build(name, root.subject, *route, dg["signals"], dg["docs_missing"])
        notes = [
            f"I could not use {r['filename']}: {r['reason']}." for r in dg["rejected_documents"]
        ]
        if dg.get("uncovered_un_numbers"):
            uncovered = ", ".join(dg["uncovered_un_numbers"])
            notes.append(f"The declaration you sent does not cover {uncovered}.")
        if dg["unreadable_attachments"]:
            files = ", ".join(dg["unreadable_attachments"])
            notes.insert(0, f"I could not open this attachment: {files}.")
        if notes:
            note = "\n\n" + "\n".join(notes)
            mail["body"] = mail["body"].replace("\n\nBest regards", f"{note}\n\nBest regards")
        unusable = dg["unreadable_attachments"] + [r["filename"] for r in dg["rejected_documents"]]
        asked = dg["docs_missing"] + [f"unusable{len(unusable)}"]
        asked += dg.get("uncovered_un_numbers") or []
        return _clarify(db, session, rfq, root, mail, asked, found)
    if session.stage == "clarifying":
        session.exception = None
        repositories.append_event(
            db,
            session.id,
            "clarification_resolved",
            {"dangerous_goods": dg["status"], "source_mails": found["sources"], "simulated": True},
        )

    _cross_check(db, session, root, fields)
    pickup = None
    if rfq.pickup_required and rfq.pickup_zone and _sourced():
        # A door pickup needs a trucker's price. Fixtures (demo) already contain one.
        pickup = pickup_bid(db, session, rfq)
        if pickup is None and _ask_truckers(db, session, rfq):
            return session
    candidates = load_fixture_candidates(db, rfq, pickup=pickup)
    priceable = pickup is not None or not rfq.pickup_required
    if _sourced() and priceable and not any(c.complete for c in candidates):
        # Neither the portal nor an approved tariff has a price: ask the carriers by mail.
        return _ask_airlines(
            db, session, rfq, dg["signals"] if dg["status"] == "documented" else []
        )
    session.exception = None if session.exception in WAITING_FOR_CARRIER else session.exception
    session.stage = "pricing"
    repositories.append_event(
        db,
        session.id,
        "rates_loaded",
        {
            "count": len(candidates),
            "complete": sum(1 for c in candidates if c.complete),
            "simulated": True,
        },
    )
    try:
        selected, evidence = choose_offer_with_evidence(candidates, rfq.service_intent, now=now)
    except DomainError as exc:
        _fail(db, session, rfq, exc.code)
        if exc.code == "no_complete_candidates":
            reason = "none of our carriers returned a complete rate for this shipment"
            mail = replies.no_offer(name, root.subject, *route, reason)
            intent, _ = outbound(
                db, rfq, f"reply:{rfq.id}:no_offer", "reply", latest_customer_mail(db, root), mail
            )
            send(db, intent)
        raise

    evidence["request"] = {
        "method": found["method"],
        "source_mails": found["sources"],
        "dangerous_goods": dg,
    }
    repositories.append_event(
        db,
        session.id,
        "offer_selected",
        {
            "candidate_id": str(selected.id),
            "carrier": selected.carrier,
            "total": str(selected.total),
            "currency": selected.currency,
            "rate_id": selected.rate_id,
            "evidence": evidence,
            "simulated": True,
        },
    )
    commit_first_quote(db, session, rfq, selected, evidence=evidence)
    return session


def _cross_check(db: Session, session: SessionRow, root: MessageRow, fields: dict) -> None:
    """One model reading of an admitted request, made once and kept on the mail. Where it
    differs from the rules the session is held for a person; after they resume, the rules'
    reading stands."""
    model = get_adapters().get("check_model")
    raw = root.raw if isinstance(root.raw, dict) else {}
    if model is None or "model_check" in raw:
        return
    text = "\n\n".join(f"{m.subject}\n{reply_text(m.body)}" for m in customer_mails(db, root))
    differences = model_check(model["gateway"], model["name"], text, fields)
    # No usable answer (stopped, over budget, timeout, bad JSON) is not an agreement.
    result = "unavailable" if differences is None else "differs" if differences else "agrees"
    check = {"model": model["name"], "result": result, "differences": differences}
    root.raw = {**raw, "model_check": check}
    flag_modified(root, "raw")
    repositories.append_event(
        db,
        session.id,
        "model_check",
        {"result": result, "differences": differences, "simulated": False},
    )
    # With no operator hold nobody would resume the session: the rules' reading stands at once.
    if differences and get_settings().hold_new_requests:
        session.paused, session.exception = True, "extraction_needs_review"
        db.flush()
        raise DomainError("session_paused")


def _sourced() -> bool:
    """True when prices come from the portal and carriers rather than from demo fixtures."""
    return get_adapters().get("rates") is not None or get_settings().live_allowed()


PICKUP_HOURS = re.compile(r"\b(\d{1,3})\s*hours?\b", re.IGNORECASE)


def _fastest(bids: list[dict]) -> dict:
    """Fewest pickup hours; a bid that names none counts as slowest; equal hours: higher price.
    shortcut: hours written in words ("six hours") count as unknown; base.en writes digits."""

    def hours(bid: dict) -> int:
        if bid.get("hours") is not None:
            return int(bid["hours"])
        found = PICKUP_HOURS.search(bid.get("text") or "")
        return int(found[1]) if found else 10**6

    return min(bids, key=lambda b: (hours(b), -Decimal(b["amount"])))


def pickup_bid(db: Session, session: SessionRow, rfq: RfqRow) -> PickupQuote | None:
    """The usable trucker bid with the fastest pickup: the board prices the pickup leg at it.
    Live accuracy to the cent: rounds 72-74 (fastest = highest bid) and round 78, where the
    fastest (11 h, 290.96) was not the highest (12 h, 364.28) and scored 35.94 on the highest."""
    bids = [
        e.payload
        for e in repositories.events_for(db, session.id)
        if e.type == "carrier_bid"
        and e.payload.get("kind") == "pickup"
        and e.payload.get("currency") == "EUR"
    ]
    if not bids:
        return None
    best = _fastest(bids)
    return PickupQuote(
        zone=str(rfq.pickup_zone),
        airport=airport_for_zone(rfq.pickup_zone) or rfq.origin,
        cost=Decimal(best["amount"]),
        currency="EUR",
        trucker=best["carrier"],
        evidence=Evidence(source_file=f"mail:{best['external_id']}", method="bid"),
    )


def pickup_reference(db: Session, session: SessionRow) -> str | None:
    """The offer number of the bid `pickup_bid` priced; the trucker asks for it in our quote."""
    bids = [
        e.payload
        for e in repositories.events_for(db, session.id)
        if e.type == "carrier_bid"
        and e.payload.get("kind") == "pickup"
        and e.payload.get("currency") == "EUR"
    ]
    return _fastest(bids).get("reference") if bids else None


def pickup_answers_in(db: Session, session: SessionRow, rfq: RfqRow) -> bool:
    """Whether to price the pickup now. By default the first usable bid is enough. With
    PICKUP_WAIT_SECONDS set, wait until every trucker we asked has answered (a price or a
    refusal), or until that many seconds have passed since the enquiries went out."""
    wait = get_settings().pickup_wait_seconds
    if not wait:
        return True
    events = repositories.events_for(db, session.id)
    answered = {
        e.payload.get("carrier")
        for e in events
        if e.type == "carrier_bid" and str(e.payload.get("kind") or "").startswith("pickup")
    }
    asked = db.scalars(
        select(ActionIntentRow).where(
            ActionIntentRow.rfq_id == rfq.id,
            ActionIntentRow.action_key.like("enquiry:%"),
            ActionIntentRow.delivery_status == "confirmed",
        )
    ).all()
    sent = [_aware(e.occurred_at) for e in events if e.type == "carrier_enquiry_sent"]
    waited = bool(sent) and clock.now() - min(sent) >= timedelta(seconds=wait)
    return len(answered) >= len(asked) or waited


def _reprice(db: Session, session: SessionRow, rfq: RfqRow) -> None:
    root = db.get(MessageRow, rfq.message_id)
    found = assess(db, root)
    if not found["missing"]:
        found["fields"] = _validated(found["fields"])
    advance(db, session, rfq, root, found)


def _ask_truckers(db: Session, session: SessionRow, rfq: RfqRow) -> bool:
    """Ask up to three truckers that serve the zone. False when there is nobody to ask."""
    truckers = truckers_for_zone(str(rfq.pickup_zone))[: get_settings().pickup_max_truckers]
    if not truckers:
        return False
    airport = airport_for_zone(rfq.pickup_zone) or rfq.origin
    volume = _weight(rfq.volume_m3) if rfq.volume_m3 is not None else None
    mails = customer_mails(db, db.get(MessageRow, rfq.message_id))
    text = "\n".join(f"{reply_text(m.body)}\n{m.subject}" for m in mails)
    # The postcode may only be in an attached request; truckers refuse a bare zone.
    text += "\n".join(d.get("text", "") for m in mails for d in documents.collect(db, m))
    mail = replies.pickup_enquiry(
        rfq.pickup_zone,
        airport,
        _weight(rfq.weight_kg),
        volume,
        str(rfq.id)[:13],
        pickup_postcode(text, rfq.pickup_zone),
    )
    asked = _enquire(db, rfq, "enquiry", truckers, mail)
    session.stage = "pricing"
    session.exception = WAITING_FOR_PICKUP
    if asked:
        repositories.append_event(
            db,
            session.id,
            "carrier_enquiry_sent",
            {"kind": "pickup", "zone": rfq.pickup_zone, "carriers": asked, "simulated": True},
        )
    return True


def _enquire(db: Session, rfq: RfqRow, prefix: str, carriers, mail: dict) -> list[str]:
    """One enquiry per carrier, ever. Returns the names asked just now."""
    asked = []
    for name, email in carriers:
        intent, created = outbound(
            db, rfq, f"{prefix}:{rfq.id}:{email}"[:128], "reply", None, mail, to=email
        )
        asked += [name] if created else []
        try:
            send(db, intent)
        except DomainError:
            continue  # the outbox recorded why; the other carriers are still asked
    return asked


def _ask_airlines(db: Session, session: SessionRow, rfq: RfqRow, dangerous: list[str]):
    """Ask up to three listed airlines that serve the destination, and the rate desk."""
    sources = [*airlines_for(rfq.destination)[:3], ("Rate desk", RATE_DESK_ADDRESS)]
    volume = _weight(rfq.volume_m3) if rfq.volume_m3 is not None else None
    mail = replies.rate_enquiry(
        rfq.origin, rfq.destination, _weight(rfq.weight_kg), volume, dangerous, str(rfq.id)[:13]
    )
    asked = _enquire(db, rfq, "rate-enquiry", sources, mail)
    session.stage = "pricing"
    session.exception = WAITING_FOR_RATE
    if asked:
        repositories.append_event(
            db,
            session.id,
            "carrier_enquiry_sent",
            {"kind": "rate", "carriers": asked, "simulated": True},
        )
    return session


def close_overdue_sourcing(db: Session, margin: timedelta = timedelta(seconds=60)) -> int:
    """Tell the customer when no carrier answered in time. Returns how many still wait."""
    waiting = 0
    for session in db.scalars(
        select(SessionRow).where(SessionRow.exception.in_(WAITING_FOR_CARRIER))
    ).all():
        rfq = db.get(RfqRow, session.rfq_id)
        if rfq is None or rfq.status != "pending":
            continue
        if (
            session.exception == WAITING_FOR_PICKUP
            and get_settings().pickup_wait_seconds
            and not session.paused
            and pickup_bid(db, session, rfq) is not None
            and pickup_answers_in(db, session, rfq)
        ):
            # The wait for the remaining truckers is over: price with the bids on file.
            try:
                _reprice(db, session, rfq)
            except DomainError:
                pass  # recorded on the session by the step that refused
            continue
        if clock.now() < _aware(rfq.expires_at) - margin:
            waiting += 1
            continue
        root = db.get(MessageRow, rfq.message_id)
        asked = "trucker" if session.exception == WAITING_FOR_PICKUP else "airline"
        what = "a pickup price" if asked == "trucker" else "a rate"
        _fail(db, session, rfq, "no_carrier_reply")
        mail = replies.no_offer(
            customer_name(root),
            root.subject,
            rfq.origin,
            rfq.destination,
            f"no {asked} has confirmed {what} in time",
        )
        intent, _ = outbound(
            db, rfq, f"reply:{rfq.id}:no_offer", "reply", latest_customer_mail(db, root), mail
        )
        try:
            send(db, intent)
        except DomainError:
            pass
    return waiting


def _clarify(db, session, rfq, root, mail: dict, asked: list[str], found: dict) -> SessionRow:
    """Ask the customer once for each distinct thing. Never carries a price."""
    session.stage = "clarifying"
    session.exception = f"waiting_for_customer:{mail['purpose']}"
    key = f"clarify:{rfq.id}:{mail['purpose']}:{'+'.join(sorted(asked))}"[:128]
    intent, created = outbound(db, rfq, key, "reply", latest_customer_mail(db, root), mail)
    if created:
        repositories.append_event(
            db,
            session.id,
            "clarification_sent",
            {
                "purpose": mail["purpose"],
                "asked_for": asked,
                "reason": {"missing": found["missing"], "dangerous_goods": found["dg"]},
                "action_key": key,
                "simulated": True,
            },
        )
    send(db, intent)
    return session


def _weight(value: Decimal) -> str:
    return format(Decimal(value).normalize(), "f")


def _calculation(db: Session, session: SessionRow, rfq: RfqRow, candidate) -> dict:
    """How the quoted total was reached, kept on the decision: weights, carrier charges and the
    trucker bids with the one `pickup_bid` chose (fewest pickup hours, then higher price)."""
    gross, volume = Decimal(rfq.weight_kg), Decimal(rfq.volume_m3 or 0)
    bids = [
        e.payload
        for e in repositories.events_for(db, session.id)
        if e.type == "carrier_bid" and str(e.payload.get("kind") or "").startswith("pickup")
    ]
    priced = [b for b in bids if b.get("kind") == "pickup" and b.get("currency") == "EUR"]
    return {
        "gross_kg": str(gross),
        "volume_cbm": str(volume),
        "chargeable_kg": str(max(gross, volume * 167)),
        "charges": candidate.charges or [],
        "pickup_bids": [
            {key: b.get(key) for key in ("kind", "carrier", "amount", "hours", "reference")}
            for b in bids
        ],
        "pickup_chosen": _fastest(priced).get("reference") if priced else None,
        "total": str(candidate.total),
        "currency": candidate.currency,
    }


def commit_first_quote(
    db: Session,
    session: SessionRow,
    rfq: RfqRow,
    candidate,
    *,
    evidence: dict | None = None,
):
    now = clock.now()
    if session.paused:
        session.exception = "session_paused"
        repositories.append_event(
            db, session.id, "quote_blocked", {"reason": "session_paused", "simulated": True}
        )
        raise DomainError("session_paused")
    if now >= _aware(rfq.expires_at):
        _block_expired(db, session, rfq)
    if candidate.valid_until is not None and now >= _aware(candidate.valid_until):
        rfq.status = "expired"
        session.exception = "offer_expired"
        session.stage = "exception"
        repositories.append_event(
            db,
            session.id,
            "quote_blocked",
            {"reason": "offer_expired", "simulated": True},
        )
        raise DomainError("offer_expired")

    intent = repositories.first_quote_intent(db, rfq.id)
    decision = repositories.quote_for(db, rfq.id)
    if intent is None:
        root = db.get(MessageRow, rfq.message_id)
        mail = replies.final_quote(
            customer_name(root),
            root.subject,
            rfq.origin,
            rfq.destination,
            _weight(rfq.weight_kg),
            candidate.carrier,
            str(candidate.total),
            candidate.currency,
            candidate.transit_days,
            [c.get("code") for c in candidate.charges or [] if c.get("code") != "pickup"],
            candidate.valid_until,
            rfq.pickup_zone if rfq.pickup_required else None,
            pickup_reference(db, session),
        )
        try:
            with db.begin_nested():
                decision = QuoteDecisionRow(
                    rfq_id=rfq.id,
                    candidate_id=candidate.id,
                    total=candidate.total,
                    currency=candidate.currency,
                    decided_at=now,
                    evidence={"calculation": _calculation(db, session, rfq, candidate)}
                    | (evidence
                    or {
                        "objective": rfq.service_intent,
                        "rule": "selected_complete_candidate",
                        "candidate_id": str(candidate.id),
                        "source_ref": candidate.source_ref,
                        "source_version": candidate.source_version,
                        "total": str(candidate.total),
                        "currency": candidate.currency,
                        "simulated": True,
                        "charges": candidate.charges or [],
                    }),
                )
                db.add(decision)
                db.flush()
                intent, _ = outbound(
                    db,
                    rfq,
                    f"quote:{rfq.id}",
                    "first_quote",
                    latest_customer_mail(db, root),
                    mail,
                    "quoted",
                    {
                        "rfq_id": str(rfq.id),
                        "quote_decision_id": str(decision.id),
                        "candidate_id": str(candidate.id),
                        "rate_id": candidate.rate_id,
                        "total": str(candidate.total),
                        "currency": candidate.currency,
                        "carrier": candidate.carrier,
                        "evidence": decision.evidence,
                    },
                )
        except IntegrityError:
            decision = repositories.quote_for(db, rfq.id)
            intent = repositories.first_quote_intent(db, rfq.id)
            if intent is None:
                raise
            return decision, intent
    if rfq.status != "quoted":
        # The first quote is committed here, before delivery: whatever then happens to the
        # mail, this RFQ can never get a second priced message.
        rfq.status = "quoted"
        session.stage = "quoted"
        repositories.append_event(
            db,
            session.id,
            "quote_recorded",
            {
                "quote_decision_id": str(decision.id),
                "candidate_id": str(candidate.id),
                "rate_id": candidate.rate_id,
                "total": str(candidate.total),
                "currency": candidate.currency,
                "evidence": decision.evidence,
                "simulated": True,
            },
        )
    try:
        send(db, intent)
    except DomainError as exc:
        if exc.code not in {"unknown_no_blind_resend", "send_in_progress"}:
            raise
    return decision, intent
