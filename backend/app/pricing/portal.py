from typing import Any

from app.contracts.models import RFQ
from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled


class FakePortal:
    def get_rate(self, rfq: RFQ, airline: str) -> dict[str, Any]:
        return {
            "simulated": True,
            "airline": airline,
            "origin": rfq.origin,
            "destination": rfq.destination,
            "total": "1250.00",
            "currency": "EUR",
            "transit_days": 5,
            "complete": True,
        }

    def book(self, rate_id: str, team_reference: str) -> dict[str, Any]:
        return {
            "simulated": True,
            "rate_id": rate_id,
            "team_reference": team_reference,
            "status": "confirmed",
        }


class LivePortal:
    def get_rate(self, rfq: RFQ, airline: str) -> dict[str, Any]:
        if not get_settings().live_allowed():
            raise LiveActionsDisabled()
        raise NotImplementedError(
            "TODO: implement GET /v1/portal/rates after re-reading official OpenAPI. "
            "Do not invent endpoints."
        )

    def book(self, rate_id: str, team_reference: str) -> dict[str, Any]:
        if not get_settings().live_allowed():
            raise LiveActionsDisabled()
        raise NotImplementedError(
            "TODO: implement POST /v1/portal/bookings after re-reading official OpenAPI. "
            "Do not invent endpoints."
        )
