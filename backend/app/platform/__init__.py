from app.core.config import get_settings
from app.core.errors import DomainError
from app.platform.booking import PortalBooking
from app.platform.gateway import FakeGateway, LiveGateway
from app.platform.jobs import process_jobs_once
from app.platform.mailbox import FakeMailbox, LiveMailbox

_installed: dict | None = None


def set_adapters(adapters: dict | None) -> None:
    """Install an explicit adapter set (the sandbox simulator does this). None removes it."""
    global _installed
    _installed = adapters


def get_adapters() -> dict:
    """mailbox, gateway and, where bookings really leave the process, booking.

    Sandbox mode never falls back to a default: without an installed simulator it fails closed.
    """
    if _installed is not None:
        return _installed
    settings = get_settings()
    if settings.app_mode == "sandbox":
        raise DomainError("sandbox_adapters_missing", "sandbox mode has no simulator installed")
    if settings.app_mode == "observe":
        # Mailbox reads only. No booking adapter and no model: nothing here can write or spend.
        return {"mailbox": LiveMailbox(), "gateway": FakeGateway()}
    if settings.live_allowed():
        return {"mailbox": LiveMailbox(), "gateway": LiveGateway(), "booking": PortalBooking()}
    return {"mailbox": FakeMailbox(), "gateway": FakeGateway()}


__all__ = [
    "FakeGateway",
    "FakeMailbox",
    "LiveGateway",
    "LiveMailbox",
    "PortalBooking",
    "get_adapters",
    "process_jobs_once",
    "set_adapters",
]
