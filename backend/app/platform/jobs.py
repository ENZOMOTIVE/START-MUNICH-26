import random
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.core.errors import DomainError
from app.db.models import MessageRow
from app.db.repositories import claim_job
from app.platform.router import route_message

PAUSED_RECHECK = timedelta(seconds=5)
RETRYABLE = frozenset({"send_failed", "send_throttled", "send_in_progress", "session_paused"})


def backoff(attempts: int) -> timedelta:
    """2, 4, 8 ... seconds, capped at a minute, plus jitter so workers do not retry in step.
    The job only becomes claimable again after this, so one cycle makes one attempt."""
    return timedelta(seconds=min(60, 2**attempts) + random.uniform(0, 1))


def process_jobs_once(db, worker_id: str, limit: int = 10) -> list:
    processed = []
    for _ in range(limit):
        job = claim_job(db, worker_id)
        if job is None:
            break
        try:
            job.status = "done"
            if job.kind == "route_message":
                route_message(db, job.payload.get("message_id"))
                message = db.get(MessageRow, UUID(str(job.payload.get("message_id"))))
                if message is not None and (message.raw or {}).get("unmatched"):
                    job.status = "needs_human"  # no agent acted on it; see GET /api/review
        except DomainError as exc:
            job.last_error = f"{exc.code}: {exc}"
            # Only a temporary condition is worth another attempt. Bad input, an expired
            # request or an uncertain send will not get better by repeating it.
            if exc.code in {"session_paused", "run_stopped"}:
                # A person holds this session (or the whole run) for as long as they need.
                # Look again shortly; a pause never uses up the job's attempts.
                job.status = "pending"
                job.run_after = datetime.now(UTC) + PAUSED_RECHECK
            elif exc.code in RETRYABLE and job.attempts < job.max_attempts:
                job.status = "pending"
                job.run_after = datetime.now(UTC) + backoff(job.attempts)
            else:
                job.status = "failed"
        except Exception as exc:
            job.last_error = str(exc)
            if job.attempts >= job.max_attempts:
                job.status = "failed"
            else:
                job.status = "pending"
                job.run_after = datetime.now(UTC) + backoff(job.attempts)
        db.flush()
        processed.append(job)
    return processed
