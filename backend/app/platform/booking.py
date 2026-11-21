"""Portal booking adapter: POST /v1/portal/bookings with `rate_id` and `team_reference`.

Shape taken from docs/contracts.md (HackathonBookingIn / HackathonBookingOut). Not yet checked
against the live API. A retry always sends the identical pair, which is how an uncertain
outcome is reconciled.
"""

from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled

BOOKINGS_PATH = "/v1/portal/bookings"
HTTP_TIMEOUT_SECONDS = 8.0


class BookingUnknown(Exception):
    """The request may have reached the portal; the outcome is not known."""

    def __init__(self, detail: str, retry_after: float | None = None):
        super().__init__(detail)
        self.retry_after = retry_after  # seconds, when the portal said when to come back


class BookingRejected(Exception):
    """The portal answered and refused."""


class PortalBooking:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client

    def _http(self) -> httpx.Client:
        settings = get_settings()
        if not settings.live_allowed():
            raise LiveActionsDisabled()
        if self._client is None:
            self._client = httpx.Client(
                base_url=settings.hackathon_api_base,
                headers={"X-API-Key": settings.hackathon_api_key},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        return self._client

    def book(self, rate_id: str, team_reference: str) -> dict[str, Any]:
        client = self._http()
        body = {"rate_id": rate_id, "team_reference": team_reference}
        try:
            response = client.post(BOOKINGS_PATH, json=body)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BookingRejected(f"not sent: {exc}") from exc
        except httpx.TransportError as exc:
            raise BookingUnknown(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 500 or response.status_code == 429:
            wait = response.headers.get("Retry-After", "")
            raise BookingUnknown(
                f"HTTP {response.status_code}", float(wait) if wait.isdigit() else None
            )
        if response.status_code >= 400:
            raise BookingRejected(f"HTTP {response.status_code}: {response.text[:200]}")
        try:
            data = response.json()
            reference = data["booking_reference"]
        except (ValueError, KeyError, TypeError) as exc:
            raise BookingUnknown(f"unreadable booking response: {exc}") from exc
        confirmed = str(data.get("status", "")).lower() == "confirmed"
        return {
            "booking_reference": str(reference),
            "status": "confirmed" if confirmed else "submitted",
            "provider_status": data.get("status"),
            "simulated": False,
        }
