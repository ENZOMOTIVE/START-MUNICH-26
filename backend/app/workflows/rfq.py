from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db import repositories
from app.db.models import ActionIntentRow, MessageRow, QuoteDecisionRow, RfqRow, SessionRow
from app.platform.mailbox import FakeMailbox
from app.platform.outbox import deliver_intent
from app.pricing.tariffs import load_fixture_candidates
from app.workflows.selection import choose_offer_with_evidence

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
    origin = str(block.get("origin") or "").strip().upper()
    destination = str(block.get("destination") or "").strip().upper()
    if not origin or not destination or block.get("weight_kg") in (None, ""):
        return None
    if len(origin) > 8 or len(destination) > 8:
        raise DomainError("invalid_route")

    try:
        weight_kg = Decimal(str(block["weight_kg"]))
    except (InvalidOperation, TypeError, ValueError) as exc:
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
        pickup_zone = str(pickup_zone).strip().upper()
        if not pickup_zone:
            pickup_zone = None
        elif len(pickup_zone) > 8:
            raise DomainError("invalid_pickup_zone")

    return {
        **block,
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
        "volume_m3": volume_m3,
        "pickup_required": pickup_required,
        "pickup_zone": pickup_zone,
        "service_intent": service_intent,
    }


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
        "expired",
        "failed",
    }:
        return existing_session
    if rfq is None:
        received_at = _aware(message.received_at)
        rfq = RfqRow(
            message_id=message.id,
            thread_id=message.thread_id,
            origin=str(block["origin"]),
            destination=str(block["destination"]),
            weight_kg=block["weight_kg"],
            volume_m3=block["volume_m3"],
            pickup_required=block["pickup_required"],
            pickup_zone=block.get("pickup_zone"),
            service_intent=str(block.get("service_intent") or "cheapest"),
            received_at=received_at,
            expires_at=received_at + RFQ_TTL,
            status="pending",
            source_ref=f"message:{message.external_id}",
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
            "volume_m3": str(rfq.volume_m3) if rfq.volume_m3 is not None else None,
            "pickup_required": rfq.pickup_required,
            "pickup_zone": rfq.pickup_zone,
            "service_intent": rfq.service_intent,
            "source_ref": rfq.source_ref,
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

    try:
        selected, evidence = choose_offer_with_evidence(candidates, rfq.service_intent)
    except DomainError as exc:
        session.stage = "exception"
        session.exception = exc.code
        rfq.status = "expired" if exc.code == "offers_expired" else "failed"
        repositories.append_event(
            db,
            session.id,
            "exception",
            {"code": exc.code, "simulated": True},
        )
        raise

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


def commit_first_quote(
    db: Session,
    session: SessionRow,
    rfq: RfqRow,
    candidate,
    *,
    evidence: dict | None = None,
):
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
        session.stage = "exception"
        repositories.append_event(
            db, session.id, "quote_blocked", {"reason": "expired", "simulated": True}
        )
        raise DomainError("expired")
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
            intent = ActionIntentRow(
                rfq_id=rfq.id,
                action_key=f"quote:{rfq.id}",
                kind="first_quote",
                payload={
                    "rfq_id": str(rfq.id),
                    "thread_id": rfq.thread_id,
                    "quote_decision_id": str(decision.id),
                    "candidate_id": str(candidate.id),
                    "rate_id": candidate.rate_id,
                    "total": str(candidate.total),
                    "currency": candidate.currency,
                    "carrier": candidate.carrier,
                    "evidence": decision.evidence,
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
    return decision, intent
