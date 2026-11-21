"""Pickup leg: 2-digit German postcode zone -> origin airport (pickup_zones.csv), and the truck charge.

The truck price itself is NOT in any tariff: it comes from a trucker bid (T3 collects those by email) or a
human. We never invent it: an RFQ that needs a pickup without a PickupQuote yields an incomplete candidate.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from . import config
from .models import ChargeLine, Evidence, RateCandidate
from .money import D, fx_to_eur, q2


@lru_cache
def zones(kit: Path | None = None) -> dict[str, dict]:
    path = (kit or config.KIT_DIR) / "pickup_zones.csv"
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r["zone"].strip().zfill(2)] = {"area": r["area"].strip(), "airport": r["airport"].strip().upper()}
    return out


def zone_from_postcode(postcode: str | int) -> str | None:
    m = re.search(r"\b(\d{5})\b", str(postcode))
    if m:
        return m.group(1)[:2]
    s = str(postcode).strip()
    return s.zfill(2) if s.isdigit() and len(s) <= 2 else None


def airport_for_zone(zone: str | int | None) -> str | None:
    if zone is None:
        return None
    hit = zones().get(str(zone).strip()[:2].zfill(2))
    return hit["airport"] if hit else None


@dataclass(frozen=True)
class PickupQuote:
    zone: str
    airport: str
    cost: Decimal                 # in `currency`
    currency: str = "EUR"
    fx_eur_per_unit: Decimal | None = None
    trucker: str | None = None
    evidence: Evidence | None = None

    def cost_eur(self) -> Decimal:
        return fx_to_eur(D(self.cost), self.currency, self.fx_eur_per_unit)


def apply_pickup(c: RateCandidate, pickup_zone: str | None, pickup: PickupQuote | None) -> RateCandidate:
    """Add the pickup leg exactly once and (re)compute the all-in total. Idempotent."""
    c.charges = [x for x in c.charges if x.code != "pickup"]
    c.missing = [m for m in c.missing if not m.startswith("pickup")]
    if pickup_zone:
        airport = airport_for_zone(pickup_zone)
        if airport is None:
            c.missing.append(f"pickup_zone_unknown:{pickup_zone}")
        elif airport != c.origin:
            c.missing.append(f"pickup_airport_mismatch:{pickup_zone}->{airport}!={c.origin}")
        elif pickup is None:
            c.missing.append("pickup")
        elif pickup.airport != airport or str(pickup.zone).zfill(2) != str(pickup_zone).zfill(2):
            c.missing.append("pickup_quote_for_other_zone")
        else:
            try:
                amt = pickup.cost_eur()
            except ValueError:
                c.missing.append("pickup_fx")
            else:
                c.charges.append(ChargeLine(
                    "pickup", f"Truck pickup zone {pickup.zone} -> {airport}" +
                    (f" ({pickup.trucker})" if pickup.trucker else ""), amt, "EUR",
                    evidence=pickup.evidence))
    c.complete = not c.missing and c.air_total is not None
    pick = sum((x.amount for x in c.charges if x.code == "pickup"), Decimal(0))
    c.total = q2(c.air_total + pick) if c.complete else None
    return c
