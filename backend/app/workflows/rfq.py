"""RFQ agent: read the request, ask for what is missing, quote once, never after the deadline.

A request moves intake -> (clarifying) -> pricing -> quoted. Only `commit_first_quote` may state
a price, and it does so at most once per RFQ. A clarification is a separate intent with its own
purpose, so asking for documents can never count as the first quote.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parseaddr

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import clock
from app.core.errors import DomainError
from app.db import repositories
from app.db.models import ActionIntentRow, MessageRow, QuoteDecisionRow, RfqRow, SessionRow
from app.platform import get_adapters
from app.platform.decisions import reply_text
from app.platform.outbox import deliver_intent
from app.pricing.tariffs import load_fixture_candidates
from app.workflows import replies
from app.workflows.extract import REQUIRED, assess_dg, merge, model_fill
from app.workflows.selection import choose_offer_with_evidence

RFQ_TTL = timedelta(minutes=6)


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
        fields, notes, method = merged["fields"], merged["notes"], "rules"
        model = get_adapters().get("extraction_model")
        gaps = [name for name in REQUIRED if name not in fields]
        if gaps and model is not None:
            text = "\n\n".join(f"{m.subject}\n{reply_text(m.body)}" for m in mails)
            found = model_fill(model["gateway"], model["name"], text, gaps)
            if found:
                fields.update(found)
                method = "rules+model"
    return {
        "fields": fields,
        "missing": [name for name in REQUIRED if name not in fields],
        "notes": notes,
        "method": method,
        "sources": [m.external_id for m in mails],
        "dg": assess_dg(
            [(f"{m.subject}\n{reply_text(m.body)}", attachments_of(m)) for m in mails]
        ),
    }


def has_event(db: Session, session_id, event_type: str, external_id: str) -> bool:
    return any(
        e.type == event_type and (e.payload or {}).get("external_id") == external_id
        for e in repositories.events_for(db, session_id)
    )


def outbound(
    db: Session,
    rfq: RfqRow,
    action_key: str,
    kind: str,
    reply_to: MessageRow,
    mail: dict,
    workflow_status: str = "pending",
    extra: dict | None = None,
) -> tuple[ActionIntentRow, bool]:
    """Get or create the one intent for this action. Returns (intent, created)."""
    intent = repositories.action_intent_by_key(db, action_key)
    if intent is not None:
        return intent, False
    external = reply_to.external_id
    payload = {
        "to": reply_to.sender,
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
                delivery_status="pending",
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
    if rfq is not None and session is not None and rfq.status in {
        "quoted",
        "accepted",
        "booked",
        "expired",
        "failed",
    }:
        quote_mail = repositories.first_quote_intent(db, rfq.id)
        if rfq.status == "quoted" and quote_mail is not None:
            send(db, quote_mail)  # only acts on a quote that provably never left
        return session

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
        if dg["unreadable_attachments"]:
            files = ", ".join(dg["unreadable_attachments"])
            note = f"\n\nI could not open this attachment: {files}."
            mail["body"] = mail["body"].replace("\n\nBest regards", f"{note}\n\nBest regards")
        asked = dg["docs_missing"] + [f"unreadable{len(dg['unreadable_attachments'])}"]
        return _clarify(db, session, rfq, root, mail, asked, found)
    if session.stage == "clarifying":
        session.exception = None
        repositories.append_event(
            db,
            session.id,
            "clarification_resolved",
            {"dangerous_goods": dg["status"], "source_mails": found["sources"], "simulated": True},
        )

    candidates = load_fixture_candidates(db, rfq)
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
        )
        try:
            with db.begin_nested():
                decision = QuoteDecisionRow(
                    rfq_id=rfq.id,
                    candidate_id=candidate.id,
                    total=candidate.total,
                    currency=candidate.currency,
                    decided_at=now,
                    evidence=evidence
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
                    },
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
