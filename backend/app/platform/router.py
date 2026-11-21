import importlib
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import NotFoundError
from app.db import repositories
from app.db.models import MessageRow
from app.platform.decisions import ReplyJudge, get_reply_judge
from app.platform.routing import AGENT_FOR_KIND, KINDS, MailFacts, RoutingDecision, decide

# Handler contract: handler(db, message) runs inside the routing job's transaction, must be
# safe to run twice for the same mail, and signals failure by raising. To attach an agent,
# add "kind": "module.path:function" here; AGENT_FOR_KIND in routing.py names the owner.
HANDLERS: dict[str, str] = {
    "rfq": "app.workflows.rfq:handle_rfq",
    "acceptance": "app.workflows.booking:handle_acceptance",
    "follow_up": "app.workflows.followup:handle_follow_up",
    "tracking": "app.workflows.followup:handle_tracking_update",
    "carrier_bid": "app.workflows.followup:handle_carrier_bid",
    "tariff": "app.workflows.followup:handle_tariff_mail",
    "booking_confirmation": "app.workflows.followup:handle_booking_confirmation",
}


def classify(message: MessageRow) -> str:
    classification = (message.classification or "").strip().lower()
    if classification in {"rfq", "acceptance"}:
        return classification
    subject = message.subject or ""
    body = (message.body or "").lower()
    if "we accept" in body:
        return "acceptance"
    if "RFQ" in subject or "rfq" in body:
        return "rfq"
    return "unknown"


def _handler(kind: str) -> Callable | None:
    target = HANDLERS.get(kind)
    if target is None:
        return None
    module_name, function_name = target.split(":")
    return getattr(importlib.import_module(module_name), function_name)


def _mail(message: MessageRow) -> dict | None:
    raw = message.raw if isinstance(message.raw, dict) else {}
    mail = raw.get("mail")
    return mail if isinstance(mail, dict) else None


def starts_thread(db: Session, message: MessageRow) -> bool:
    mail = _mail(message)
    if mail is not None:
        return not mail.get("references")
    earlier = exists().where(
        MessageRow.thread_id == message.thread_id,
        MessageRow.id != message.id,
        MessageRow.received_at <= message.received_at,
    )
    return not db.execute(select(earlier)).scalar()


def mail_facts(db: Session, message: MessageRow) -> MailFacts:
    mail = _mail(message) or {}
    session = repositories.session_by_thread(db, message.thread_id)
    quote = None
    if session is not None and session.stage == "quoted":
        quote = repositories.quote_for(db, session.rfq_id)
    return MailFacts(
        sender=message.sender or "",
        subject=message.subject or "",
        body=message.body or "",
        has_attachments=bool(mail.get("attachments")),
        starts_thread=starts_thread(db, message),
        preset_kind=(message.classification or "").strip().lower() or None,
        thread_stage=session.stage if session is not None else None,
        quote_total=str(quote.total) if quote is not None else None,
        quote_currency=quote.currency if quote is not None else None,
    )


def _note_on_timeline(db: Session, message: MessageRow, decision: RoutingDecision) -> None:
    session = repositories.session_by_thread(db, message.thread_id)
    if session is None:
        return
    repositories.append_event(
        db,
        session.id,
        "mail_routed",
        {
            "external_id": message.external_id,
            "kind": decision.kind,
            "agent": decision.agent,
            "decided_by": decision.decided_by,
            "sender": decision.sender.get("email"),
            "simulated": bool(message.simulated),
        },
        simulated=bool(message.simulated),
        occurred_at=message.received_at,
    )


def route_message(db: Session, message_id: UUID | str, judge: ReplyJudge | None = None):
    if not isinstance(message_id, UUID):
        message_id = UUID(str(message_id))
    message = db.get(MessageRow, message_id)
    if message is None:
        raise NotFoundError(f"message {message_id}")

    raw = dict(message.raw) if isinstance(message.raw, dict) else {}
    previous = raw.get("routing")
    if isinstance(previous, dict) and previous.get("kind") in KINDS:
        kind = previous["kind"]
        decision = None
    else:
        decision = decide(mail_facts(db, message), judge or get_reply_judge(db))
        kind = decision.kind
        raw["routing"] = decision.record()
        message.raw = raw
        flag_modified(message, "raw")
        message.classification = kind
        db.flush()

    handler = _handler(kind)
    result = handler(db, message) if handler is not None else None
    if result is None and decision is not None:
        # No agent acted on this mail: keep it visible on the thread's timeline.
        _note_on_timeline(db, message, decision)
        db.flush()
    return result


__all__ = ["AGENT_FOR_KIND", "HANDLERS", "classify", "route_message"]
