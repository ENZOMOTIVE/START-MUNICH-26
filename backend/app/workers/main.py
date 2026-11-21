import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import session_scope
from app.platform.jobs import process_jobs_once
from app.platform.mailbox import LiveMailbox, MailboxPoller


def main() -> None:
    configure_logging()
    settings = get_settings()
    poller = None
    if settings.live_allowed():
        poller = MailboxPoller(LiveMailbox(), settings.mailbox_poll_seconds)
    while True:
        if poller is not None:
            poller.tick(session_scope)
        with session_scope() as db:
            process_jobs_once(db, settings.worker_id)
        time.sleep(settings.job_poll_seconds)


if __name__ == "__main__":
    main()
