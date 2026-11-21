from app.core.config import get_settings
from app.platform.gateway import FakeGateway, LiveGateway
from app.platform.jobs import process_jobs_once
from app.platform.mailbox import FakeMailbox, LiveMailbox


def get_adapters() -> dict:
    if get_settings().live_allowed():
        return {"mailbox": LiveMailbox(), "gateway": LiveGateway()}
    return {"mailbox": FakeMailbox(), "gateway": FakeGateway()}


__all__ = [
    "FakeGateway",
    "FakeMailbox",
    "LiveGateway",
    "LiveMailbox",
    "get_adapters",
    "process_jobs_once",
]
