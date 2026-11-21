from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.models import MessageRow


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


def route_message(db: Session, message_id: UUID | str):
    if not isinstance(message_id, UUID):
        message_id = UUID(str(message_id))
    message = db.get(MessageRow, message_id)
    if message is None:
        raise NotFoundError(f"message {message_id}")
    kind = classify(message)
    if kind == "rfq":
        from app.workflows.rfq import handle_rfq

        return handle_rfq(db, message)
    if kind == "acceptance":
        from app.workflows.booking import handle_acceptance

        return handle_acceptance(db, message)
    return None
