"""Portal adapters (`app.contracts.adapters.PortalAdapter`).

* FakePortal  - demo/test double. Returns a documented `HackathonRate`-shaped dict, never touches
the network.
* LivePortal  - GET /v1/portal/rates only (documented in docs/contracts.md). Read-only: `book()` is
T3's and
                stays disabled here. Reads are gated by `live_allowed()` (APP_MODE=live and
                LIVE_ACTIONS_ENABLED=true) unless the caller explicitly opts in with
                `allow_reads=True`
                (used by the CLI / opt-in live test; still GET only).

`rate_candidate()` turns a portal rate into the team `RateCandidate` with simulated=False.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import httpx

from app.contracts.models import RFQ, RateCandidate
from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled

from .contract import _get, option_to_contract, rfq_id_of
from .money import D
from .portal_client import candidate_from_rate
from .settings import api_settings

RATES_PATH = "/v1/portal/rates"


class FakePortal:
    def get_rate(self, rfq: RFQ, airline: str) -> dict[str, Any]:
        return {
            "simulated": True,
            "rate_id": "R-DEMOYELLOW01",
            "airline": "Yellowtail Air Freight",
            "airline_code": airline,
            "origin": rfq.origin,
            "destination": rfq.destination,
            "weight_kg": str(rfq.weight_kg),
            "chargeable_weight_kg": str(rfq.weight_kg),
            "total": "382.75",
            "currency": "EUR",
            "fx_eur_per_usd": None,
            "transit_days": 5,
            "valid_until": "2027-01-01T00:00:00Z",
            "dataset_version": "demo-2026-11",
        }

    def book(self, rate_id: str, team_reference: str) -> dict[str, Any]:
        return {
            "simulated": True,
            "rate_id": rate_id,
            "team_reference": team_reference,
            "status": "confirmed",
        }


class LivePortal:
    def __init__(
        self, *, allow_reads: bool = False, http: httpx.Client | None = None, timeout_s: float = 8.0
    ):
        self.allow_reads = allow_reads
        self._http = http
        self.timeout_s = timeout_s

    def _client(self) -> httpx.Client:
        if self._http is None:
            base, key = api_settings()
            self._http = httpx.Client(
                base_url=base, headers={"X-API-Key": key}, timeout=self.timeout_s
            )
        return self._http

    def _check(self) -> None:
        if not (self.allow_reads or get_settings().live_allowed()):
            raise LiveActionsDisabled()

    def get_rate(self, rfq: RFQ, airline: str) -> dict[str, Any]:
        """GET /v1/portal/rates for one airline. Raises httpx.HTTPStatusError on non-2xx."""
        self._check()
        vol = _get(rfq, "volume_m3")
        params = {
            "origin": str(_get(rfq, "origin")).upper(),
            "destination": str(_get(rfq, "destination")).upper(),
            "airline": airline,
            "weight_kg": str(D(str(_get(rfq, "weight_kg")))),
            "volume_cbm": str(D(str(vol))) if vol not in (None, "") else "0",
        }
        r = self._client().get(RATES_PATH, params=params)
        r.raise_for_status()
        data = r.json()
        data["_fetched_at"] = dt.datetime.now(dt.UTC).isoformat()
        return data

    def book(self, rate_id: str, team_reference: str) -> dict[str, Any]:
        if not get_settings().live_allowed():
            raise LiveActionsDisabled()
        raise NotImplementedError(
            "Booking (POST /v1/portal/bookings) is T3's. T2's portal adapter is read-only."
        )

    def close(self) -> None:
        if self._http is not None:
            self._http.close()


def rate_candidate(
    rate: dict[str, Any], rfq: Any, *, now: dt.datetime | None = None
) -> RateCandidate:
    """Portal rate JSON -> team RateCandidate (simulated=False unless the rate says it is
    simulated)."""
    vol = _get(rfq, "volume_m3")
    opt = candidate_from_rate(
        rate,
        D(str(_get(rfq, "weight_kg"))),
        D(str(vol)) if vol not in (None, "") else Decimal(0),
        now=now,
    )
    from .pickup import apply_pickup

    apply_pickup(
        opt, _get(rfq, "pickup_zone") if _get(rfq, "pickup_required", False) else None, None
    )
    return option_to_contract(
        opt, rfq_id_of(rfq), now=now, simulated=bool(rate.get("simulated", False))
    )
