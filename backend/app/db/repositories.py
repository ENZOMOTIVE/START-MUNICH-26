from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import clock
from app.db.models import (
    ActionIntentRow,
    BookingRow,
    JobRow,
    MailboxCursorRow,
    MessageRow,
    QuoteDecisionRow,
    RateCandidateRow,
    RfqRow,
    SessionEventRow,
    SessionRow,
)

JOB_LEASE = timedelta(seconds=60)


def get_or_create_message(db: Session, values: dict) -> tuple[MessageRow, bool]:
    stmt = (
        insert(MessageRow)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["external_id"])
        .returning(MessageRow.id)
    )
    inserted_id = db.execute(stmt).scalar_one_or_none()
    row = db.execute(
        select(MessageRow).where(MessageRow.external_id == values["external_id"])
    ).scalar_one()
    return row, inserted_id is not None


def enqueue_job(db: Session, kind: str, payload: dict) -> JobRow:
    job = JobRow(kind=kind, payload=payload, status="pending")
    db.add(job)
    db.flush()
    return job


def claim_job(db: Session, worker_id: str) -> JobRow | None:
    now = datetime.now(UTC)
    stmt = (
        select(JobRow)
        .where(
            or_(
                JobRow.status == "pending",
                # A worker that died mid-job leaves it `claimed`; take it back after the lease.
                and_(JobRow.status == "claimed", JobRow.claimed_at < now - JOB_LEASE),
            ),
            JobRow.run_after <= now,
        )
        .order_by(JobRow.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    job = db.execute(stmt).scalar_one_or_none()
    if job is None:
        return None
    job.status = "claimed"
    job.claimed_at = datetime.now(UTC)
    job.claimed_by = worker_id
    job.attempts += 1
    db.flush()
    return job


def get_cursor(db: Session, name: str) -> int:
    row = db.get(MailboxCursorRow, name)
    return 0 if row is None else row.since_id


def advance_cursor(db: Session, name: str, since_id: int) -> None:
    row = db.get(MailboxCursorRow, name)
    if row is None:
        db.add(MailboxCursorRow(name=name, since_id=since_id))
    elif since_id > row.since_id:
        row.since_id = since_id
    db.flush()


def rfq_by_message(db: Session, message_id: UUID) -> RfqRow | None:
    return db.execute(select(RfqRow).where(RfqRow.message_id == message_id)).scalar_one_or_none()


def session_by_rfq(db: Session, rfq_id: UUID) -> SessionRow | None:
    return db.execute(select(SessionRow).where(SessionRow.rfq_id == rfq_id)).scalar_one_or_none()


def session_by_thread(db: Session, thread_id: str) -> SessionRow | None:
    return db.execute(
        select(SessionRow)
        .join(RfqRow, RfqRow.id == SessionRow.rfq_id)
        .where(RfqRow.thread_id == thread_id)
        .order_by(SessionRow.created_at.desc())
    ).scalars().first()


def first_quote_intent(db: Session, rfq_id: UUID) -> ActionIntentRow | None:
    return db.execute(
        select(ActionIntentRow).where(
            ActionIntentRow.rfq_id == rfq_id, ActionIntentRow.kind == "first_quote"
        )
    ).scalar_one_or_none()


def action_intent_by_key(db: Session, action_key: str) -> ActionIntentRow | None:
    return db.execute(
        select(ActionIntentRow).where(ActionIntentRow.action_key == action_key)
    ).scalar_one_or_none()


def append_event(
    db: Session,
    session_id: UUID,
    event_type: str,
    payload: dict,
    *,
    simulated: bool = True,
    occurred_at: datetime | None = None,
) -> SessionEventRow:
    event = SessionEventRow(
        session_id=session_id,
        type=event_type,
        payload=payload,
        occurred_at=occurred_at or clock.now(),  # simulated time in the sandbox, wall time else
        simulated=simulated,
    )
    db.add(event)
    db.flush()
    return event


def list_sessions(db: Session) -> list[SessionRow]:
    return list(db.execute(select(SessionRow).order_by(SessionRow.created_at.desc())).scalars())


def events_for(db: Session, session_id: UUID) -> list[SessionEventRow]:
    return list(
        db.execute(
            select(SessionEventRow)
            .where(SessionEventRow.session_id == session_id)
            .order_by(SessionEventRow.occurred_at, SessionEventRow.id)
        ).scalars()
    )


def candidates_for(db: Session, rfq_id: UUID) -> list[RateCandidateRow]:
    return list(
        db.execute(select(RateCandidateRow).where(RateCandidateRow.rfq_id == rfq_id)).scalars()
    )


def quote_for(db: Session, rfq_id: UUID) -> QuoteDecisionRow | None:
    return db.execute(
        select(QuoteDecisionRow).where(QuoteDecisionRow.rfq_id == rfq_id)
    ).scalar_one_or_none()


def booking_for(db: Session, rfq_id: UUID) -> BookingRow | None:
    return db.execute(select(BookingRow).where(BookingRow.rfq_id == rfq_id)).scalar_one_or_none()
