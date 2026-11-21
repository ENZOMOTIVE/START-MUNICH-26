"""Hard limits for one supervised live run. Off unless RUN_LIMITS=true.

Counters and the admitted-thread list live in the cursor table (like `last_send_ms`), so the
API, the worker and a restart all see the same numbers. A counter counts attempts, not
successes, and is committed before the external call it pays for.
"""

from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.errors import DomainError
from app.db.models import MailboxCursorRow

GRACE = timedelta(minutes=5)  # admitted work may finish this long after the admission window
COUNTERS = ("rfqs", "mails", "bookings", "model_calls", "tokens_in", "tokens_out", "micro_usd")


def _key(name: str) -> str:
    return f"run:{name}"[:64]


def _value(db: Session, name: str) -> int | None:
    row = db.get(MailboxCursorRow, _key(name))
    return None if row is None else row.since_id


def _now_ms() -> int:
    return int(clock.now().timestamp() * 1000)


def spend(db: Session, name: str, limit: int | None = None, amount: int = 1) -> None:
    """Count `amount` against `limit` (default: RUN_MAX_<NAME>) in one atomic statement, or
    refuse."""
    settings = get_settings()
    if not settings.run_limits:
        return
    limit = getattr(settings, f"run_max_{name}") if limit is None else limit
    key = _key(name)
    db.execute(insert(MailboxCursorRow).values(name=key, since_id=0).on_conflict_do_nothing())
    counted = db.execute(
        update(MailboxCursorRow)
        .where(MailboxCursorRow.name == key, MailboxCursorRow.since_id + amount <= limit)
        .values(since_id=MailboxCursorRow.since_id + amount)
        .execution_options(synchronize_session=False)
    ).rowcount
    if not counted:
        raise DomainError("run_budget_exhausted", f"{name}: limit {limit} reached")


def admit(db: Session, thread_id: str) -> None:
    """Put a customer thread on the allowlist. The first admission starts the run's clock."""
    settings = get_settings()
    if not settings.run_limits or _value(db, f"admitted:{thread_id}") is not None:
        return
    if _value(db, "stopped") is not None:
        raise DomainError("run_stopped", "this run was stopped; nothing more is admitted")
    started = _value(db, "started_ms")
    if started is not None and _now_ms() >= started + settings.run_minutes * 60_000:
        raise DomainError("run_closed", "the admission window of this run has ended")
    spend(db, "rfqs")
    if started is None:
        db.add(MailboxCursorRow(name=_key("started_ms"), since_id=_now_ms()))
    db.add(MailboxCursorRow(name=_key(f"admitted:{thread_id}"), since_id=_now_ms()))
    db.flush()


def require_admitted(db: Session, thread_id: str) -> None:
    """Refuse any external action for a thread that is not on this run's allowlist."""
    settings = get_settings()
    if not settings.run_limits:
        return
    if _value(db, "stopped") is not None:
        raise DomainError("run_stopped", "this run's outbound actions are stopped")
    if _value(db, f"admitted:{thread_id}") is None:
        raise DomainError("not_admitted", f"thread {thread_id} is not admitted to this run")
    window = timedelta(minutes=settings.run_minutes) + GRACE
    if _now_ms() >= (_value(db, "started_ms") or 0) + int(window.total_seconds() * 1000):
        raise DomainError("run_closed", "this run has ended; pending work is kept")


def stop(db: Session) -> None:
    """Stop every outbound action of this run. Pending work stays where it is."""
    if _value(db, "stopped") is None:
        db.add(MailboxCursorRow(name=_key("stopped"), since_id=_now_ms()))
        db.flush()


def reserve_model_call(db: Session, tokens_in: int, tokens_out: int) -> int:
    """Reserve one model call at its worst case before it is made. Returns micro-USD reserved."""
    settings = get_settings()
    if not settings.run_limits:
        return 0
    if settings.model_price_in_usd <= 0 or settings.model_price_out_usd <= 0:
        raise DomainError("model_price_unverified", "no verified price is configured")
    # USD per 1M tokens is micro-USD per token; round up so a reservation never undercounts.
    price_in, price_out = settings.model_price_in_usd, settings.model_price_out_usd
    cost = int(tokens_in * price_in + tokens_out * price_out) + 1
    spend(db, "model_calls")
    spend(db, "tokens_in", settings.run_max_tokens_in, tokens_in)
    spend(db, "tokens_out", settings.run_max_tokens_out, tokens_out)
    spend(db, "micro_usd", int(settings.run_max_spend_usd * 1_000_000), cost)
    return cost


def status(db: Session) -> dict:
    settings = get_settings()
    rows = db.query(MailboxCursorRow).filter(MailboxCursorRow.name.like("run:%")).all()
    values = {row.name[4:]: row.since_id for row in rows}
    return {
        "enabled": settings.run_limits,
        "started_ms": values.get("started_ms"),
        "stopped_ms": values.get("stopped"),
        "minutes": settings.run_minutes,
        "admitted_threads": sorted(k[9:] for k in values if k.startswith("admitted:")),
        "used": {name: values.get(name, 0) for name in COUNTERS},
        "limits": {
            "rfqs": settings.run_max_rfqs,
            "mails": settings.run_max_mails,
            "bookings": settings.run_max_bookings,
            "model_calls": settings.run_max_model_calls,
            "tokens_in": settings.run_max_tokens_in,
            "tokens_out": settings.run_max_tokens_out,
            "micro_usd": int(settings.run_max_spend_usd * 1_000_000),
        },
    }
