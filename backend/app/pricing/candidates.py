"""T2 entry point: RFQ -> list[RateCandidate] (team contract). Deterministic, zero LLM/gateway calls.

Order of sources:
  1. Fixtures (FIXTURES_DIR/rates/*.json). Always preferred while live actions are off
     (APP_MODE=demo or LIVE_ACTIONS_ENABLED=false) - the demo never touches the network.
  2. Live (only when `live_allowed()` or `live=True`): portal calculator, GET /v1/portal/rates for every
     airline on the lane (+ weight-break pivot probe), merged with Decimal-engine prices from the approved
     tariff snapshot (follow-up parser output) for carriers that only quote by email.
     Pickup legs need a real trucker price (`pickup`), otherwise those candidates are complete=False.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.models import RateCandidate
from app.core.config import get_settings

from .contract import _get, option_to_contract, rfq_id_of
from .fixtures import candidates_from_fixtures
from .money import D
from .pickup import PickupQuote

log = logging.getLogger("app.pricing.candidates")


def _price_request(rfq: Any):
    from .engine import PriceRequest

    vol = _get(rfq, "volume_m3")
    intent = _get(rfq, "service_intent") or "cheapest"
    intent = getattr(intent, "value", intent)
    zone = (
        _get(rfq, "pickup_zone")
        if _get(rfq, "pickup_required", False) or _get(rfq, "pickup_zone")
        else None
    )
    return PriceRequest(
        destination=str(_get(rfq, "destination")).upper(),
        gross_kg=D(str(_get(rfq, "weight_kg"))),
        volume_m3=D(str(vol)) if vol not in (None, "") else Decimal(0),
        origin=str(_get(rfq, "origin")).upper() if _get(rfq, "origin") else None,
        pickup_zone=str(zone) if zone else None,
        prefer=str(intent).lower(),
    )


async def live_candidates_async(
    rfq: Any,
    *,
    pickup: PickupQuote | None = None,
    portal=None,
    use_tariffs: bool = True,
    snapshot: Path | None = None,
    now: dt.datetime | None = None,
) -> list[RateCandidate]:
    from .portal_client import PortalClient
    from .tariff_candidates import TariffBook, build_candidates

    now = now or dt.datetime.now(dt.UTC)
    book = None
    if use_tariffs:
        try:
            book = TariffBook.from_snapshot(snapshot)
        except (OSError, ValueError) as e:
            log.warning("tariff snapshot unavailable: %s", e)
    own = portal is None
    portal = portal or PortalClient()
    try:
        cs = await build_candidates(_price_request(rfq), book, portal, pickup=pickup, now=now)
    finally:
        if own:
            await portal.aclose()
    for f in cs.failures:
        log.info("portal failure %s", f.to_dict())
    rid = rfq_id_of(rfq)
    return [option_to_contract(o, rid, now=now) for o in cs.candidates]


def build_rate_candidates(
    rfq: Any,
    *,
    live: bool | None = None,
    pickup: PickupQuote | None = None,
    fixtures_dir: str | Path | None = None,
    now: dt.datetime | None = None,
) -> list[RateCandidate]:
    """Fixtures first; live portal/tariff pricing only when live actions are allowed (or live=True)."""
    live_ok = get_settings().live_allowed() if live is None else live
    if not live_ok:
        return candidates_from_fixtures(rfq, fixtures_dir=fixtures_dir, now=now)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(live_candidates_async(rfq, pickup=pickup, now=now))
    raise RuntimeError(
        "build_rate_candidates() called inside an event loop; await live_candidates_async() instead"
    )
