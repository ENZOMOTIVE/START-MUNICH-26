"""Deterministic air pricing from an approved tariff lane. Decimal only, no LLM.

  chargeable = max(gross_kg, volume_m3 x divisor)               divisor = tariff's stated (167 in the kit)
  bracket    = the highest break <= chargeable                  (a bracket runs from its break to the next)
  for the applicable bracket AND every higher break b:          <- the weight-break pivot
      billed  = chargeable if b is the applicable break else b
      freight = max(billed x rate(b), minimum)                 minimum applies to freight BEFORE surcharges
      fuel    = fuel_per_kg x billed      security = security_per_kg x billed   (billed-weight basis)
      total   = freight + fuel + security
  pick the lowest total (tie -> lower billed weight).

Rounding: ROUND_HALF_UP to cents PER CHARGE LINE, total = sum of rounded lines ("per_line", default).
Evidence: the live portal does exactly this (21 Nov, CGN-DEL JP at 3.71/kg all-in: 100.3 kg -> 372.12, not
372.11 = round(372.113); 100.1 kg -> 371.37, so not round-up). rounding="total" quantizes once at the end.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from .money import D, chargeable_weight, q2, to_kg, to_m3, volume_from_dims
from .parsers.base import LaneRate
from .quote_models import PricedCharge, PricedOption


@dataclass(frozen=True)
class PriceRequest:
    destination: str
    gross_kg: Decimal
    volume_m3: Decimal = Decimal(0)
    origin: str | None = None  # origin airport; derived from pickup_zone if absent
    pickup_zone: str | None = None  # set => door pickup required => pickup leg must be priced
    prefer: str = "cheapest"  # cheapest | fastest
    ship_date: dt.date | None = None

    @classmethod
    def from_fixture(cls, d: dict) -> PriceRequest:
        from .pickup import airport_for_zone, zone_from_postcode

        w = d["weight"]
        gross = to_kg(str(w["value"]), w.get("unit", "kg")) if isinstance(w, dict) else D(str(w))
        vol = Decimal(0)
        if "volume" in d and d["volume"] is not None:
            v = d["volume"]
            vol = to_m3(str(v["value"]), v.get("unit", "m3")) if isinstance(v, dict) else D(str(v))
        elif "dims" in d:
            for p in d["dims"]:
                vol += volume_from_dims(
                    str(p["l"]),
                    str(p["w"]),
                    str(p["h"]),
                    p.get("unit", "cm"),
                    int(p.get("pieces", 1)),
                )
        zone = d.get("pickup_zone")
        if not zone and d.get("pickup_postcode"):
            zone = zone_from_postcode(d["pickup_postcode"])
        origin = d.get("origin") or (airport_for_zone(zone) if zone else None)
        sd = d.get("ship_date")
        return cls(
            destination=d["destination"].upper(),
            gross_kg=gross,
            volume_m3=vol,
            origin=origin.upper() if origin else None,
            pickup_zone=zone,
            prefer=d.get("prefer", "cheapest"),
            ship_date=dt.date.fromisoformat(sd) if sd else None,
        )


@dataclass
class BreakOption:
    break_kg: int
    billed_kg: Decimal
    rate: Decimal
    raw_freight: Decimal
    freight: Decimal
    min_applied: bool
    fuel: Decimal
    security: Decimal

    @property
    def total(self) -> Decimal:
        return self.freight + self.fuel + self.security


@dataclass
class TariffPrice:
    chargeable_kg: Decimal
    applicable_break: int
    best: BreakOption
    options: list[BreakOption] = field(default_factory=list)

    @property
    def pivoted(self) -> bool:
        return self.best.break_kg != self.applicable_break

    @property
    def total(self) -> Decimal:
        return q2(self.best.total)


def applicable_break(breaks: list[int], kg: Decimal) -> int:
    cur = breaks[0]
    for b in breaks:
        if kg >= b:
            cur = b
    return cur


ROUNDING = "per_line"


def price_lane(
    lane: LaneRate, gross_kg, volume_m3=0, pivot: bool = True, rounding: str | None = None
) -> TariffPrice:
    rounding = rounding or ROUNDING
    rl = q2 if rounding == "per_line" else (lambda x: x)  # noqa: E731
    gross = D(gross_kg)
    ch = chargeable_weight(gross, volume_m3, lane.divisor_kg_per_m3)
    if ch <= 0:
        raise ValueError("chargeable weight must be > 0")
    rates = dict(lane.breaks)
    brks = sorted(rates)
    start = applicable_break(brks, ch)
    opts = []
    for b in brks:
        if b < start or (b > start and not pivot):
            continue
        billed = ch if b == start else max(ch, Decimal(b))
        raw = billed * rates[b]
        freight = max(raw, lane.min_charge) if lane.min_before_surcharges else raw
        basis = billed if lane.surcharge_basis == "billed" else gross
        opts.append(
            BreakOption(
                b,
                billed,
                rates[b],
                raw,
                rl(freight),
                freight > raw,
                rl(lane.fuel_per_kg * basis),
                rl(lane.security_per_kg * basis),
            )
        )
    if not lane.min_before_surcharges:  # minimum against the all-in total (not used by the kit)
        for o in opts:
            o.freight = rl(max(o.raw_freight, lane.min_charge - o.fuel - o.security))
            o.min_applied = o.freight > o.raw_freight
    best = min(opts, key=lambda o: (o.total, o.billed_kg))
    return TariffPrice(ch, start, best, opts)


def tariff_candidate(
    lane: LaneRate, req: PriceRequest, pivot: bool = True, rounding: str | None = None
) -> PricedOption:
    from .money import fx_to_eur

    p = price_lane(lane, req.gross_kg, req.volume_m3, pivot, rounding)
    o = p.best
    ev = lambda k: lane.evidence.get(k)  # noqa: E731
    missing = []
    fx = None
    if lane.currency != "EUR":
        missing.append(f"fx:{lane.currency}")  # sheet gives no FX: never guess one
    charges = [
        PricedCharge(
            "air_freight",
            f"Air freight {o.billed_kg} kg x {o.rate}/kg (break {o.break_kg} kg)"
            + (f", minimum {lane.min_charge} applied" if o.min_applied else ""),
            o.freight,
            lane.currency,
            o.billed_kg,
            o.rate,
            evidence=ev(f"break_{o.break_kg}") if not o.min_applied else ev("min_charge"),
        ),
        PricedCharge(
            "fuel",
            f"Fuel surcharge {lane.fuel_per_kg}/kg",
            o.fuel,
            lane.currency,
            o.billed_kg if lane.surcharge_basis == "billed" else D(req.gross_kg),
            lane.fuel_per_kg,
            evidence=ev("fuel_per_kg"),
        ),
        PricedCharge(
            "security",
            f"Security surcharge {lane.security_per_kg}/kg",
            o.security,
            lane.currency,
            o.billed_kg if lane.surcharge_basis == "billed" else D(req.gross_kg),
            lane.security_per_kg,
            evidence=ev("security_per_kg"),
        ),
    ]
    air = None
    if not missing:
        air = q2(fx_to_eur(o.total, lane.currency, fx))
    c = PricedOption(
        carrier=lane.carrier,
        origin=lane.origin,
        destination=lane.destination,
        source="tariff",
        total=None,
        charges=charges,
        chargeable_weight_kg=p.chargeable_kg,
        billed_weight_kg=o.billed_kg,
        transit_days=lane.transit_days,
        rate_id=None,
        valid_until=None,
        dataset_version=lane.version,
        source_links=list(lane.source_files),
        source_total=q2(o.total),
        source_currency=lane.currency,
        air_total=air,
        pivoted=p.pivoted,
        break_kg=o.break_kg,
        missing=missing,
        evidence={
            "set_id": lane.set_id,
            "tariff_valid": [lane.valid_from.isoformat(), lane.valid_to.isoformat()],
            "divisor_kg_per_m3": str(lane.divisor_kg_per_m3),
            "rounding": rounding or ROUNDING,
            "applicable_break": p.applicable_break,
            "pivot_options": [
                {
                    "break_kg": x.break_kg,
                    "billed_kg": str(x.billed_kg),
                    "total": str(q2(x.total)),
                    "min_applied": x.min_applied,
                }
                for x in p.options
            ],
            "booking": "no portal rate_id: book by email to the carrier",
        },
    )
    if req.ship_date and not (lane.valid_from <= req.ship_date <= lane.valid_to):
        c.missing.append("tariff_not_valid_on_ship_date")
    from .pickup import apply_pickup

    return apply_pickup(c, req.pickup_zone, None)
