import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db import repositories
from app.db.models import MessageRow

log = logging.getLogger(__name__)

CURSOR_NAME = "mailbox"
POLL_LIMIT = 100
HTTP_TIMEOUT_SECONDS = 5.0
DEFAULT_RATE_LIMIT_WAIT_SECONDS = 10.0


class MailboxError(Exception):
    def __init__(self, kind: str, detail: str, retry_after: float | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail
        self.retry_after = retry_after


class FakeMailbox:
    def poll(self, since_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return []

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"simulated": True, "id": "fake"}


class LiveMailbox:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    def _http(self) -> httpx.Client:
        settings = get_settings()
        if not settings.live_allowed():
            raise LiveActionsDisabled()
        if self._client is None:
            self._client = httpx.Client(
                base_url=settings.hackathon_api_base,
                headers={"X-API-Key": settings.hackathon_api_key},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        return self._client

    def _get(self, path: str, **params: Any) -> httpx.Response:
        client = self._http()
        try:
            response = client.get(path, params=params or None)
        except httpx.TransportError as exc:
            raise MailboxError("network", str(exc)) from exc
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise MailboxError(
                "rate_limited",
                "HTTP 429",
                float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        if response.status_code >= 400:
            raise MailboxError("http_error", f"HTTP {response.status_code}")
        return response

    def poll(self, since_id: int, limit: int = POLL_LIMIT) -> dict[str, Any] | list[Any]:
        return self._get("/v1/mailbox/messages", since_id=since_id, limit=limit).json()

    def download_attachment(self, message_id: int | str, attachment_id: int | str) -> bytes:
        path = f"/v1/mailbox/messages/{int(message_id)}/attachments/{int(attachment_id)}"
        return self._get(path).content

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


# Field names confirmed from a live poll on 2026-11-21 (the 5U spec leaves items untyped).
_SENDER_FIELDS = ("from",)
_RECIPIENT_FIELDS = ("to",)
_SUBJECT_FIELDS = ("subject",)
_BODY_FIELDS = ("body",)
_RECEIVED_FIELDS = ("received_at",)


def _first(item: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return None


def _address(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("email") or value.get("address") or "")
    if isinstance(value, list):
        return _address(value[0]) if value else ""
    return str(value or "")


def _timestamp(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def map_poll_item(item: dict[str, Any]) -> dict[str, Any]:
    mail_id = str(item["id"])
    references = item.get("references") or []
    return {
        "external_id": mail_id,
        "thread_id": str(references[0]) if references else mail_id,
        "sender": _address(_first(item, _SENDER_FIELDS)),
        "recipient": _address(_first(item, _RECIPIENT_FIELDS)),
        "subject": str(_first(item, _SUBJECT_FIELDS) or ""),
        "body": str(_first(item, _BODY_FIELDS) or ""),
        "received_at": _timestamp(_first(item, _RECEIVED_FIELDS)),
        "classification": "unknown",
        "source": "mailbox",
        "simulated": False,
        "raw": {"mail": item},
    }


def poll_page(response: dict[str, Any] | list[Any]) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(response, list):
        return [i for i in response if isinstance(i, dict)], None
    next_since_id = response.get("next_since_id")
    for value in response.values():
        if isinstance(value, list) and all(isinstance(i, dict) for i in value):
            items = value
            break
    else:
        items = []
    return items, int(next_since_id) if next_since_id is not None else None


def poll_mailbox_once(db: Session, mailbox, limit: int = POLL_LIMIT) -> int:
    since_id = repositories.get_cursor(db, CURSOR_NAME)
    items, next_since_id = poll_page(mailbox.poll(since_id, limit))
    for item in items:
        ingest_message(db, map_poll_item(item))
    if next_since_id is None:
        next_since_id = max((int(i["id"]) for i in items), default=since_id)
    repositories.advance_cursor(db, CURSOR_NAME, next_since_id)
    return len(items)


class MailboxPoller:
    def __init__(
        self,
        mailbox,
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.mailbox = mailbox
        self.interval_seconds = interval_seconds
        self.clock = clock
        self.next_poll_at = 0.0

    def tick(self, session_scope) -> int | None:
        now = self.clock()
        if now < self.next_poll_at:
            return None
        self.next_poll_at = now + self.interval_seconds
        try:
            with session_scope() as db:
                return poll_mailbox_once(db, self.mailbox)
        except MailboxError as exc:
            if exc.kind == "rate_limited":
                wait = exc.retry_after or DEFAULT_RATE_LIMIT_WAIT_SECONDS
                self.next_poll_at = max(self.next_poll_at, now + wait)
            log.warning("mailbox poll failed: %s", exc)
        except Exception:
            log.exception("mailbox poll failed")
        return None
