"""Transport for observation mode: reads pass, everything else is refused before dispatch."""

import httpx

from app.core.errors import DomainError

READ_METHODS = frozenset({"GET", "HEAD"})
# Paths that cost money or change remote state even with GET are never allowed either.
FORBIDDEN_PATHS = ("/v1/gateway/",)


class ObservationOnly(DomainError):
    def __init__(self, what: str):
        super().__init__("observation_only", f"observation mode refused: {what}")


class ReadOnlyTransport(httpx.BaseTransport):
    """Wraps the real transport. A write never reaches it, so it never reaches the network."""

    def __init__(self, inner: httpx.BaseTransport):
        self._inner = inner
        self.refused: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method not in READ_METHODS or path.startswith(FORBIDDEN_PATHS):
            self.refused.append(f"{request.method} {path}")
            raise ObservationOnly(f"{request.method} {path}")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()
