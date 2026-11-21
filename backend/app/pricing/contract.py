"""Map internal pricing results onto the team contract (`app.contracts.models.RateCandidate`).

This is the ONLY output shape T2 hands to T3. Internal working types (`PricedOption` / `PricedCharge` in
quote_models.py) never leave this package.

    internal                         -> contract
    PricedOption.complete/missing    -> complete (False if anything missing, total unknown or expired)
    PricedOption.dataset_version     -> source_version
    PricedOption.source_links[0]     -> source_ref
    PricedCharge (included in total) -> ChargeLine(code, amount quantized to cents, currency, unit)
    simulated = False (real portal/tariff data); only fixtures / FakePortal are simulated=True
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from app.contracts.models import ChargeLine, RateCandidate

from .money import CENT, D, q2
from .quote_models import PricedOption


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def rfq_id_of(rfq: Any) -> uuid.UUID:
    rid = _get(rfq, "id")
    if isinstance(rid, uuid.UUID):
        return rid
    if rid:
        return uuid.UUID(str(rid))
    return uuid.uuid4()


def parse_ts(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        t = value
    else:
        t = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def option_to_contract(
    opt: PricedOption,
    rfq_id: uuid.UUID,
    *,
    now: dt.datetime | None = None,
    margin_s: int = 30,
    simulated: bool | None = None,
) -> RateCandidate:
    now = now or dt.datetime.now(dt.UTC)
    lines = [c for c in opt.charges if c.included_in_total]
    charges = [
        ChargeLine(code=c.code, amount=q2(c.amount), currency=c.currency, unit="shipment")
        for c in lines
    ]
    total = opt.total
    complete = bool(
        opt.complete and not opt.missing and total is not None and not opt.is_expired(now, margin_s)
    )
    if total is None:
        # Contract requires a number. An incomplete quote carries the known part (air leg) and
        # complete=False so T3's select_offer never picks it.
        total = opt.air_total if opt.air_total is not None else Decimal("0")
    return RateCandidate(
        id=uuid.uuid4(),
        rfq_id=rfq_id,
        carrier=opt.carrier,
        carrier_code=opt.carrier_code,
        total=q2(total),
        currency=opt.currency,
        transit_days=opt.transit_days,
        rate_id=opt.rate_id,
        complete=complete,
        valid_until=opt.valid_until,
        source_ref=(opt.source_links[0] if opt.source_links else opt.source),
        source_version=opt.dataset_version or "unknown",
        charges=charges,
        simulated=False if simulated is None else simulated,
    )


def charges_sum(charges: list[ChargeLine]) -> Decimal:
    return sum((D(c.amount).quantize(CENT) for c in charges), Decimal("0"))
