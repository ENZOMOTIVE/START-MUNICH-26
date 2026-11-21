import time

from sqlalchemy import text

from app.core import netguard
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine, session_scope  # noqa: I001
from app.platform import get_adapters
from app.platform.jobs import process_jobs_once
from app.platform.mailbox import LiveMailbox, MailboxPoller
from app.platform.outbox import deliver_pending, expire_stale_sends
from app.workflows.booking import retry_open_bookings
from app.workflows.rfq import close_overdue_sourcing

WORKER_LOCK = 5_150_002  # advisory lock id: one mailbox-polling worker per database


def claim_single_worker():
    """Take the one-worker lock on its own connection, or stop. The first rollout runs one
    worker; quota and send spacing are kept in that process."""
    connection = get_engine().connect()
    if not connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK}):
        connection.close()
        raise SystemExit("another worker already holds the worker lock; run exactly one")
    return connection


def main() -> None:
    configure_logging()
    settings = get_settings()
    if settings.app_mode == "sandbox":
        netguard.install(settings.database_url)
    poller = None
    if settings.live_allowed() or settings.app_mode == "observe":
        # Held for the life of this process. A second worker stops here instead of sharing
        # the mailbox, the send budget and the portal quota with the first.
        _only_worker = claim_single_worker()  # noqa: F841 - the open connection is the lock
        poller = MailboxPoller(LiveMailbox(), settings.mailbox_poll_seconds)
    while True:
        if poller is not None:
            poller.tick(session_scope)
        with session_scope() as db:
            process_jobs_once(db, settings.worker_id)
        if settings.live_allowed():
            # Recovery passes: a send that outlived its lease becomes UNKNOWN, and a booking
            # whose answer was lost is asked again with the identical identity.
            with session_scope() as db:
                expire_stale_sends(db)
                deliver_pending(db, get_adapters()["mailbox"])
                retry_open_bookings(db)
                close_overdue_sourcing(db)
        time.sleep(settings.job_poll_seconds)


if __name__ == "__main__":
    main()
