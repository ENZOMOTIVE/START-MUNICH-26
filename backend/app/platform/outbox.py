"""Send one action intent through the mailbox, recording honestly what is known afterwards.

`sending` is committed before the external call. If the process dies after that, nobody can
tell whether the mail left, so once the send lease has passed the intent becomes UNKNOWN.
UNKNOWN is never resent automatically. A unique action key stops a second intent; it does not
make the external send exactly-once.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db import repositories
from app.db.models import ActionIntentRow, SessionRow
from app.platform.mailbox import MailboxError

SEND_LEASE = timedelta(seconds=30)  # longer than any mailbox call; a live sender finishes first


def _record(db: Session, intent: ActionIntentRow, session, status: str, event: str, detail: str):
    intent.delivery_status = status
    if session is not None:
        if status == "UNKNOWN":
            session.exception = "send_unknown"
        repositories.append_event(
            db,
            session.id,
            event,
            {
                "action_key": intent.action_key,
                "kind": intent.kind,
                "purpose": (intent.payload or {}).get("purpose"),
                "delivery_status": status,
                "detail": detail,
                "simulated": bool(intent.simulated),
            },
        )
    db.commit()


def _interrupted(intent: ActionIntentRow) -> bool:
    started = (intent.payload or {}).get("_sending_at")
    return not started or clock.now() - datetime.fromisoformat(started) >= SEND_LEASE


def deliver_intent(db: Session, intent: ActionIntentRow, mailbox) -> ActionIntentRow:
    session = repositories.session_by_rfq(db, intent.rfq_id)
    if intent.delivery_status == "sending":
        if not _interrupted(intent):
            raise DomainError("send_in_progress")
        _record(db, intent, session, "UNKNOWN", "send_unknown", "interrupted while sending")
    if intent.delivery_status == "UNKNOWN":
        raise DomainError("unknown_no_blind_resend")
    if intent.delivery_status == "confirmed":
        return intent
    if session is not None:
        # Read the committed flag: an operator's pause must beat a stale copy in this session.
        if db.scalar(select(SessionRow.paused).where(SessionRow.id == session.id)):
            raise DomainError("session_paused")
    payload = dict(intent.payload or {})
    payload["_sending_at"] = clock.now().isoformat()
    intent.payload = payload
    flag_modified(intent, "payload")
    intent.delivery_status = "sending"
    db.commit()
    try:
        result = mailbox.send(intent.payload or {})
    except MailboxError as exc:
        if exc.kind == "unknown":
            _record(db, intent, session, "UNKNOWN", "send_unknown", exc.detail)
            raise DomainError("unknown_no_blind_resend") from exc
        status = "failed" if exc.kind == "rejected" else "pending"
        _record(db, intent, session, status, "send_failed", f"{exc.kind}: {exc.detail}")
        raise DomainError("send_failed") from exc
    simulated = not get_settings().live_allowed()
    intent.delivery_status = "confirmed"
    intent.simulated = simulated
    payload = dict(intent.payload or {})
    payload["_send"] = {"simulated": simulated, "id": (result or {}).get("id")}
    payload.setdefault("simulated", simulated)
    intent.payload = payload
    flag_modified(intent, "payload")
    db.flush()
    return intent


def expire_stale_sends(db: Session) -> int:
    """Mark every send that outlived its lease UNKNOWN. Run on a timer by the worker."""
    stale = [
        intent
        for intent in db.scalars(
            select(ActionIntentRow).where(ActionIntentRow.delivery_status == "sending")
        )
        if intent.kind != "booking" and _interrupted(intent)
    ]
    for intent in stale:
        session = repositories.session_by_rfq(db, intent.rfq_id)
        _record(db, intent, session, "UNKNOWN", "send_unknown", "interrupted while sending")
    return len(stale)


def retry_intent(db: Session, intent: ActionIntentRow, mailbox) -> ActionIntentRow:
    return deliver_intent(db, intent, mailbox)
