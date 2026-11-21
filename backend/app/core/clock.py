"""One clock for workflow decisions, so the sandbox can run simulated time."""

from collections.abc import Callable
from datetime import UTC, datetime


def _wall() -> datetime:
    return datetime.now(UTC)


_source: Callable[[], datetime] = _wall


def now() -> datetime:
    return _source()


def set_clock(source: Callable[[], datetime] | None = None) -> None:
    global _source
    _source = source or _wall
