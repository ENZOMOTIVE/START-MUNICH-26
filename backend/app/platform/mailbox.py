import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db import repositories
from app.db.models import MessageRow


class FakeMailbox:
    def poll(self, since_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"simulated": True, "id": "fake"}


class LiveMailbox:
    def poll(self, since_id: int, limit: int = 100) -> dict[str, Any]:
        if not get_settings().live_allowed():
            raise LiveActionsDisabled()
        raise NotImplementedError(
            "TODO: implement GET https://api.start-hack.5u.ai/v1/mailbox/messages "
            "after re-reading official OpenAPI. Do not invent endpoints."
        )

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not get_settings().live_allowed():
            raise LiveActionsDisabled()
        raise NotImplementedError(
            "TODO: implement POST https://api.start-hack.5u.ai/v1/mailbox/messages "
            "after re-reading official OpenAPI. Do not invent endpoints."
        )


def ingest_message(db: Session, payload: dict[str, Any]) -> tuple[MessageRow, bool]:
    received_at = payload.get("received_at") or datetime.now(UTC)
    if isinstance(received_at, str):
        received_at = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    values = {
        "id": payload.get("id") or uuid.uuid4(),
        "external_id": payload["external_id"],
        "thread_id": payload["thread_id"],
        "sender": payload.get("sender", ""),
        "recipient": payload.get("recipient", ""),
        "subject": payload.get("subject", ""),
        "body": payload.get("body", ""),
        "received_at": received_at,
        "classification": payload.get("classification", "unknown"),
        "source": payload.get("source", "mailbox"),
        "simulated": payload.get("simulated", True),
        "raw": payload.get("raw") or {},
    }
    row, created = repositories.get_or_create_message(db, values)
    if created:
        repositories.enqueue_job(db, kind="route_message", payload={"message_id": str(row.id)})
    return row, created
