from app.core.errors import DomainError
from app.db.repositories import claim_job
from app.platform.router import route_message


def process_jobs_once(db, worker_id: str, limit: int = 10) -> list:
    processed = []
    for _ in range(limit):
        job = claim_job(db, worker_id)
        if job is None:
            break
        try:
            if job.kind == "route_message":
                route_message(db, job.payload.get("message_id"))
            job.status = "done"
        except DomainError as exc:
            job.last_error = f"{exc.code}: {exc}"
            if exc.code == "unknown_no_blind_resend" or job.attempts >= job.max_attempts:
                job.status = "failed"
            else:
                job.status = "pending"
        except Exception as exc:
            job.last_error = str(exc)
            if job.attempts >= job.max_attempts:
                job.status = "failed"
            else:
                job.status = "pending"
        db.flush()
        processed.append(job)
    return processed
