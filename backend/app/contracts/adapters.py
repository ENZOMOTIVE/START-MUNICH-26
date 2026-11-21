from typing import Any, Protocol

from app.contracts.models import RFQ, Booking, RateCandidate


class MailboxAdapter(Protocol):
    def poll(self, since_id: int, limit: int = 100) -> dict[str, Any]:
        """Official live shape: GET /v1/mailbox/messages?since_id&limit (max 200)."""

    def send(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Official live shape: POST /v1/mailbox/messages."""


class PortalAdapter(Protocol):
    def get_rate(self, rfq: RFQ, airline: str) -> dict[str, Any]:
        """Official live shape: GET /v1/portal/rates."""

    def book(self, rate_id: str, team_reference: str) -> dict[str, Any]:
        """Official live shape: POST /v1/portal/bookings."""


class CarrierAdapter(Protocol):
    def enquire(self, rfq: RFQ, carrier_email: str) -> dict[str, Any]:
        """Email enquiry placeholder. Live send goes through the mailbox adapter."""


class GatewayAdapter(Protocol):
    def complete(self, prompt: str, model: str | None = None) -> dict[str, Any]:
        """Official live shape: POST /v1/gateway/chat/completions."""


class BookingAdapter(Protocol):
    def confirm(self, candidate: RateCandidate, team_reference: str) -> Booking:
        """Portal book or documented email fallback. Demo is always simulated."""
