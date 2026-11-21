from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.errors import DomainError
from app.db.models import ActionIntentRow
from app.db.repositories import session_by_rfq


def deliver_intent(db: Session, intent: ActionIntentRow, mailbox) -> ActionIntentRow:
    if intent.delivery_status == "UNKNOWN":
        raise DomainError("unknown_no_blind_resend")
    if intent.delivery_status == "confirmed":
        return intent
    session = session_by_rfq(db, intent.rfq_id)
    if session is not None and session.paused and intent.delivery_status == "pending":
        raise DomainError("session_paused")
    mailbox.send(intent.payload or {})
    intent.delivery_status = "confirmed"
    intent.simulated = True
    payload = dict(intent.payload or {})
    payload["_send"] = {"simulated": True}
    payload.setdefault("simulated", True)
    intent.payload = payload
    flag_modified(intent, "payload")
    db.flush()
    return intent


def retry_intent(db: Session, intent: ActionIntentRow, mailbox) -> ActionIntentRow:
    return deliver_intent(db, intent, mailbox)
