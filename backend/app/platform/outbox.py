"""Send one action intent through the mailbox, recording honestly what is known afterwards.

`sending` is committed before the external call. If the process dies after that, nobody can
tell whether the mail left, so once the send lease has passed the intent becomes UNKNOWN.
UNKNOWN is never resent automatically. A unique action key stops a second intent; it does not
make the external send exactly-once.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db import repositories
from app.db.models import ActionIntentRow, RfqRow, SessionRow
from app.platform import runlimits
from app.platform.mailbox import MailboxError

LAST_SEND = "last_send_ms"  # stored in the cursor table: time of the last send, in ms
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


def deliver_intent(
    db: Session, intent: ActionIntentRow, mailbox, *, operator: bool = False
) -> ActionIntentRow:
    """`operator=True` is a person sending from a paused (taken-over) session. It goes through
    the same `sending` claim, so the agent and a person can never both send one action."""
    session = repositories.session_by_rfq(db, intent.rfq_id)
    if intent.delivery_status == "sending":
        if not _interrupted(intent):
            raise DomainError("send_in_progress")
        _record(db, intent, session, "UNKNOWN", "send_unknown", "interrupted while sending")
    if intent.delivery_status == "UNKNOWN":
        raise DomainError("unknown_no_blind_resend")
    if intent.delivery_status == "confirmed":
        return intent
    if intent.delivery_status == "observed":
        raise DomainError("observation_only", "a proposal from observation mode is never sent")
    runlimits.require_running(db)  # the global stop, for the agents and for an operator
    not_before = (intent.payload or {}).get("_not_before")
    if not_before and clock.now() < datetime.fromisoformat(not_before):
        raise DomainError("send_throttled")  # the mailbox said when to come back (Retry-After)
    if session is not None:
        # Read the committed flag: an operator's pause must beat a stale copy in this session.
        paused = db.scalar(select(SessionRow.paused).where(SessionRow.id == session.id))
        if paused and not operator:
            raise DomainError("session_paused")
        if operator and not paused:
            raise DomainError("take_over_first", "pause the session before sending by hand")
    rfq = db.get(RfqRow, intent.rfq_id)
    if rfq is not None:
        # A bounded live run sends only for threads an operator admitted (RUN_LIMITS).
        runlimits.require_admitted(db, rfq.thread_id)
    if intent.kind == "first_quote" and rfq is not None:
        expires = rfq.expires_at if rfq.expires_at.tzinfo else rfq.expires_at.replace(tzinfo=UTC)
        if clock.now() >= expires:
            # However this send was reached (retry, restart, sweep): no quote after the deadline.
            _record(db, intent, session, "failed", "send_failed", "request expired before sending")
            raise DomainError("expired")
        answered = getattr(mailbox, "round_answered", None)
        if answered is not None and answered(rfq.received_at):
            # Only the first quote in a thread counts, and another sender of this team
            # (another machine, another codebase) has already answered this request.
            if session is not None:
                session.exception = "answered_elsewhere"
            _record(db, intent, session, "failed", "send_failed", "the board shows an answer")
            raise DomainError("answered_elsewhere")
    settings = get_settings()
    if settings.live_allowed() and settings.send_min_interval_ms:
        # shortcut: two workers can read the same value and both send; add a row lock if more
        # than one worker ever sends.
        now_ms = int(clock.now().timestamp() * 1000)
        if now_ms - repositories.get_cursor(db, LAST_SEND) < settings.send_min_interval_ms:
            raise DomainError("send_throttled")
        repositories.advance_cursor(db, LAST_SEND, now_ms)
    runlimits.spend(db, "mails")  # an attempt, committed with the claim below
    payload = dict(intent.payload or {})
    payload["_sending_at"] = clock.now().isoformat()
    # The claim is one conditional UPDATE: of two senders that both read `pending` (two
    # workers, or the agent and an operator), exactly one changes the row and goes on.
    claimed = db.execute(
        update(ActionIntentRow)
        .where(ActionIntentRow.id == intent.id, ActionIntentRow.delivery_status == "pending")
        .values(delivery_status="sending", payload=payload)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    db.refresh(intent)
    if not claimed:
        raise DomainError("send_in_progress")
    try:
        result = mailbox.send(intent.payload or {})
    except MailboxError as exc:
        if exc.kind == "unknown":
            _record(db, intent, session, "UNKNOWN", "send_unknown", exc.detail)
            raise DomainError("unknown_no_blind_resend") from exc
        status = "failed" if exc.kind == "rejected" else "pending"
        if exc.retry_after:
            wait = clock.now() + timedelta(seconds=exc.retry_after)
            intent.payload = {**(intent.payload or {}), "_not_before": wait.isoformat()}
            flag_modified(intent, "payload")
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


def deliver_pending(db: Session, mailbox, threads: list[str] | None = None) -> int:
    """Send every mail that is still `pending` (throttled, rate-limited or never reached the
    mailbox). `threads` limits it to those customer threads."""
    query = select(ActionIntentRow).where(
        ActionIntentRow.delivery_status == "pending", ActionIntentRow.kind != "booking"
    )
    if threads is not None:
        query = query.where(ActionIntentRow.payload["thread_id"].astext.in_(threads))
    sent = 0
    for intent in db.scalars(query.order_by(ActionIntentRow.created_at)).all():
        if "body" not in (intent.payload or {}):
            continue
        try:
            deliver_intent(db, intent, mailbox)
            sent += 1
        except DomainError as exc:
            if exc.code == "send_throttled":
                break  # the next pass continues
    return sent


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
