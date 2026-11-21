"""Merge tariff + portal (+ carrier bids) into RateCandidates for T3, ranked by the customer's preference.

python -m app.pricing.tariff_candidates tests/pricing/fixtures/rfq_cgn_del_80kg_pickup.json [--live] [--pickup-cost 45.00]
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from . import settings as config
from .engine import PriceRequest, tariff_candidate
from .money import D, MoneyError, fx_to_eur, q2
from .parsers.base import LaneRate
from .pickup import PickupQuote, airport_for_zone, apply_pickup
from .quote_models import Evidence, PricedCharge, PricedOption, ReviewException


class TariffBook:
    """Approved tariff lanes indexed by (origin, destination)."""

    def __init__(self, records: list[LaneRate]):
        self.records = records
        self.index: dict[tuple[str, str], list[LaneRate]] = {}
        for r in records:
            self.index.setdefault((r.origin, r.destination), []).append(r)

    @classmethod
    def from_snapshot(cls, path: Path | None = None) -> TariffBook:
        d = json.loads(Path(path or config.SNAPSHOT).read_text())
        return cls([LaneRate.from_dict(r) for s in d["sets"] for r in s["records"]])

    @classmethod
    def from_store(cls, root: Path | None = None) -> TariffBook:
        from .parsers.store import TariffStore

        return cls(TariffStore(root or config.STORE_DIR).current_records())

    @classmethod
    def from_kit(cls, kit: Path | None = None) -> TariffBook:
        from .parsers.ingest import ingest

        res, _ = ingest(kit)
        return cls([r for s in res for r in s.records])

    def lanes(self, origin: str, dest: str) -> list[LaneRate]:
        return self.index.get((origin, dest), [])


def candidate_from_bid(
    carrier: str,
    origin: str,
    dest: str,
    total,
    currency: str = "EUR",
    *,
    fx_eur_per_unit=None,
    transit_days: int | None = None,
    chargeable_kg=None,
    valid_until: dt.datetime | None = None,
    evidence: Evidence | None = None,
    includes: tuple[str, ...] = ("air_freight", "fuel", "security"),
) -> PricedOption:
    """A carrier's emailed all-in bid as a candidate (T3 parses the mail; this keeps one contract)."""
    missing = []
    if not {"air_freight", "fuel", "security"} <= set(includes):
        missing.append(
            "bid_not_all_in:"
            + ",".join(sorted({"air_freight", "fuel", "security"} - set(includes)))
        )
    tot = D(total)
    air = None
    try:
        air = fx_to_eur(tot, currency, D(fx_eur_per_unit) if fx_eur_per_unit is not None else None)
    except MoneyError:
        missing.append(f"fx:{currency}")
    c = PricedOption(
        carrier=carrier,
        origin=origin,
        destination=dest,
        source="bid",
        total=None,
        charges=[
            PricedCharge("air_all_in", f"{carrier} emailed bid", air, "EUR", evidence=evidence)
        ]
        if air is not None
        else [],
        chargeable_weight_kg=D(chargeable_kg) if chargeable_kg is not None else None,
        transit_days=transit_days,
        valid_until=valid_until,
        source_total=tot,
        source_currency=currency,
        fx_eur_per_unit=D(fx_eur_per_unit) if fx_eur_per_unit else None,
        air_total=q2(air) if air is not None else None,
        missing=missing,
        evidence={"bid": evidence.to_dict() if evidence else None, "includes": list(includes)},
    )
    return c


def rank(cands: list[PricedOption], prefer: str = "cheapest") -> list[PricedOption]:
    """Cheapest: by all-in total (air total when pickup still missing - identical pickup for all), then transit.
    Fastest: by transit days, then price. Deterministic tie-break on carrier name."""

    def price(c):
        return (
            c.total
            if c.total is not None
            else (c.air_total if c.air_total is not None else Decimal("1e12"))
        )

    def transit(c):
        return c.transit_days if c.transit_days is not None else 10**6

    key = (
        (lambda c: (transit(c), price(c), c.carrier))
        if prefer == "fastest"
        else (lambda c: (price(c), transit(c), c.carrier))
    )
    out = sorted(cands, key=key)
    for c in out:
        c.tags = [t for t in c.tags if t not in ("cheapest", "fastest")]
    priced = [c for c in out if c.air_total is not None and "expired" not in c.missing]
    if priced:
        min(priced, key=lambda c: (price(c), transit(c), c.carrier)).tags.append("cheapest")
        min(priced, key=lambda c: (transit(c), price(c), c.carrier)).tags.append("fastest")
    return out


@dataclass
class CandidateSet:
    request: PriceRequest
    candidates: list[PricedOption] = field(default_factory=list)
    exceptions: list[ReviewException] = field(default_factory=list)
    failures: list = field(default_factory=list)
    generated_at: dt.datetime | None = None

    def eligible(self, now: dt.datetime | None = None) -> list[PricedOption]:
        now = now or dt.datetime.now(dt.UTC)
        return [c for c in self.candidates if c.eligible(now)]

    def best(self, now: dt.datetime | None = None) -> PricedOption | None:
        e = rank(self.eligible(now), self.request.prefer)
        return e[0] if e else None

    def to_dict(self, now: dt.datetime | None = None) -> dict:
        now = now or dt.datetime.now(dt.UTC)
        r = self.request
        best = self.best(now)
        return {
            "request": {
                "origin": r.origin,
                "destination": r.destination,
                "gross_kg": str(r.gross_kg),
                "volume_m3": str(r.volume_m3),
                "pickup_zone": r.pickup_zone,
                "prefer": r.prefer,
            },
            "generated_at": (self.generated_at or now).isoformat(),
            "best": best.to_dict(now) if best else None,
            "candidates": [c.to_dict(now) for c in self.candidates],
            "exceptions": [e.to_dict() for e in self.exceptions],
            "portal_failures": [f.to_dict() for f in self.failures],
        }


async def build_candidates(
    req: PriceRequest,
    book: TariffBook | None = None,
    portal=None,
    *,
    pickup: PickupQuote | None = None,
    bids: list[PricedOption] | None = None,
    now: dt.datetime | None = None,
    pivot_probes: int = 1,
) -> CandidateSet:
    now = now or dt.datetime.now(dt.UTC)
    cs = CandidateSet(req, generated_at=now)
    origin = req.origin or (airport_for_zone(req.pickup_zone) if req.pickup_zone else None)
    if not origin:
        cs.exceptions.append(
            ReviewException(
                "request",
                "origin_unknown",
                f"no origin airport (pickup_zone={req.pickup_zone!r})",
                scope="request",
            )
        )
        return cs
    if (
        req.origin
        and req.pickup_zone
        and airport_for_zone(req.pickup_zone) not in (None, req.origin)
    ):
        cs.exceptions.append(
            ReviewException(
                "request",
                "origin_zone_mismatch",
                f"origin {req.origin} but pickup zone {req.pickup_zone} maps to {airport_for_zone(req.pickup_zone)}",
                scope="request",
            )
        )
    req2 = PriceRequest(
        req.destination,
        req.gross_kg,
        req.volume_m3,
        origin,
        req.pickup_zone,
        req.prefer,
        req.ship_date or now.date(),
    )
    cs.request = req2
    cands: list[PricedOption] = []
    if book is not None:
        for lane in book.lanes(origin, req.destination):
            cands.append(tariff_candidate(lane, req2))
    if portal is not None:
        from .portal_client import portal_quotes

        pq = await portal_quotes(
            portal,
            origin,
            req.destination,
            req.gross_kg,
            req.volume_m3,
            pivot_probes=pivot_probes,
            now=now,
        )
        cands += pq.candidates
        cs.failures += pq.failures
    for b in bids or []:
        if b.origin == origin and b.destination == req.destination:
            cands.append(b)
    for c in cands:
        apply_pickup(c, req.pickup_zone, pickup)
    cs.candidates = rank(cands, req.prefer)
    return cs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fixture", type=Path)
    ap.add_argument("--live", action="store_true", help="also query the portal (read-only GETs)")
    ap.add_argument("--snapshot", type=Path, default=config.SNAPSHOT)
    ap.add_argument(
        "--pickup-cost", help="EUR truck cost for the request's pickup zone (e.g. a trucker bid)"
    )
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    fx = json.loads(a.fixture.read_text())
    req = PriceRequest.from_fixture(fx)
    pickup = None
    if a.pickup_cost and req.pickup_zone:
        pickup = PickupQuote(
            req.pickup_zone,
            airport_for_zone(req.pickup_zone),
            D(a.pickup_cost),
            evidence=Evidence("cli --pickup-cost", method="manual"),
        )
    book = TariffBook.from_snapshot(a.snapshot) if a.snapshot.exists() else TariffBook.from_kit()

    async def run():
        portal = None
        if a.live:
            from .portal_client import PortalClient

            portal = PortalClient()
        try:
            return await build_candidates(req, book, portal, pickup=pickup)
        finally:
            if portal:
                await portal.aclose()

    cs = asyncio.run(run())
    data = cs.to_dict()
    txt = json.dumps(data, indent=1)
    if a.out:
        a.out.write_text(txt + "\n")
    else:
        print(txt)
    now = dt.datetime.now(dt.UTC)
    print(
        f"# {len(cs.candidates)} candidates, {len(cs.eligible(now))} eligible, "
        f"{len(cs.failures)} portal failures",
        file=__import__("sys").stderr,
    )
    for c in cs.candidates:
        print(
            f"#  {c.source:6} {c.carrier:26} total={c.total} air={c.air_total} transit={c.transit_days} "
            f"tags={c.tags} missing={c.missing} rate_id={c.rate_id}",
            file=__import__("sys").stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
