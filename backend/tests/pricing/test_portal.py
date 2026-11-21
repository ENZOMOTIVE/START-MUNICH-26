"""Portal client against a mocked transport (no network): retries, isolation, FX, expiry, merge."""
import asyncio
import datetime as dt
import json
from decimal import Decimal

import httpx

from app.pricing.tariff_candidates import TariffBook, build_candidates, candidate_from_bid, rank
from app.pricing.engine import PriceRequest
from app.pricing.pickup import PickupQuote
from app.pricing.portal_client import PortalClient, candidate_from_rate, portal_quotes

NOW = dt.datetime(2026, 11, 21, 15, 0, tzinfo=dt.timezone.utc)
LATER = (NOW + dt.timedelta(minutes=15)).isoformat()

PORTS = {"lanes": [{"origin": "FRA", "destination": "ORD",
                    "airlines": [{"code": "AA", "name": "Alpha Cargo"}, {"code": "BB", "name": "Beta Cargo"},
                                 {"code": "CC", "name": "Gamma Cargo"}]}]}


def rate_json(code, total, kg="80", currency="EUR", fx=None, valid=LATER, transit=3):
    d = {"rate_id": f"R-{code}-{kg}", "airline": f"{code} Air", "airline_code": code, "origin": "FRA",
         "destination": "ORD", "weight_kg": kg, "chargeable_weight_kg": kg, "total": total,
         "currency": currency, "transit_days": transit, "valid_until": valid, "dataset_version": "v1"}
    if fx:
        d["fx_eur_per_usd"] = fx
    return d


def client(handler, **kw):
    http = httpx.AsyncClient(base_url="https://portal.test", transport=httpx.MockTransport(handler))
    return PortalClient(http=http, **kw)


def test_rate_429_then_success_and_503_isolated():
    calls = {"AA": 0}

    def handler(req: httpx.Request):
        p = req.url.path
        if p == "/v1/portal/ports":
            return httpx.Response(200, json=PORTS)
        if p == "/v1/portal/brackets":
            return httpx.Response(200, json={"airline": "x", "airline_code": "x", "breaks_kg": [0, 45, 100, 300]})
        a = req.url.params["airline"]
        kg = req.url.params["weight_kg"]
        hdr = {"X-RateLimit-Limit": "10", "X-RateLimit-Remaining": "5"}
        if a == "AA":
            calls["AA"] += 1
            if calls["AA"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})
            return httpx.Response(200, headers=hdr, json=rate_json("AA", "400.00" if kg == "80" else "390.00", kg))
        if a == "BB":
            return httpx.Response(503, json={"detail": "email beta@x instead"})
        return httpx.Response(200, headers=hdr, json=rate_json("CC", "350.00", kg))

    async def run():
        async with client(handler, max_retries=2) as c:
            return await portal_quotes(c, "FRA", "ORD", Decimal("80"), Decimal("0"), now=NOW)
    pq = asyncio.run(run())
    got = {c.carrier_code: c for c in pq.candidates}
    assert set(got) == {"AA", "CC"}                       # BB failed, others still priced
    assert any(f.airline == "BB" and f.kind == "unavailable" for f in pq.failures)
    # AA pivot probe at 100 kg (100 <= 80*1.6) returned 390.00 < 400.00 -> pivoted rate wins
    assert got["AA"].air_total == Decimal("390.00") and got["AA"].pivoted and got["AA"].rate_id == "R-AA-100"


def test_timeout_does_not_stall_others():
    async def handler(req: httpx.Request):
        p = req.url.path
        if p == "/v1/portal/ports":
            return httpx.Response(200, json=PORTS)
        if req.url.params.get("airline") == "AA":
            await asyncio.sleep(5)
        return httpx.Response(200, json=rate_json(req.url.params["airline"], "300.00"))

    async def run():
        async with client(handler, timeout_s=0.3, max_retries=0) as c:
            return await portal_quotes(c, "FRA", "ORD", Decimal("80"), pivot_probes=0, now=NOW)
    pq = asyncio.run(run())
    assert {c.carrier_code for c in pq.candidates} == {"BB", "CC"}
    assert [f.kind for f in pq.failures] == ["timeout"]


def test_fx_and_total_not_readded():
    c = candidate_from_rate(rate_json("US", "1000.00", currency="USD", fx="0.9234"), Decimal("80"), now=NOW)
    # portal total is all-in air (freight+fuel+security): converted once, nothing added on top
    assert c.air_total == Decimal("923.40") and c.source_total == Decimal("1000.00")
    assert [x.code for x in c.charges] == ["air_all_in"]
    missing_fx = candidate_from_rate(rate_json("US", "1000.00", currency="USD"), Decimal("80"), now=NOW)
    assert "fx:USD" in missing_fx.missing and missing_fx.air_total is None


def test_expired_rate_not_bookable():
    old = (NOW - dt.timedelta(seconds=1)).isoformat()
    c = candidate_from_rate(rate_json("AA", "300.00", valid=old), Decimal("80"), now=NOW)
    assert "expired" in c.missing and not c.bookable(NOW) and not c.eligible(NOW)
    fresh = candidate_from_rate(rate_json("AA", "300.00"), Decimal("80"), now=NOW)
    from app.pricing.pickup import apply_pickup
    apply_pickup(fresh, None, None)
    assert fresh.bookable(NOW) and fresh.eligible(NOW)
    assert not fresh.bookable(NOW + dt.timedelta(minutes=15))   # expires later


def test_missing_required_fields_block_complete():
    r = rate_json("AA", "300.00")
    del r["valid_until"]
    c = candidate_from_rate(r, Decimal("80"), now=NOW)
    from app.pricing.pickup import apply_pickup
    apply_pickup(c, None, None)
    assert not c.complete and "valid_until" in c.missing


def test_merge_tariff_portal_bid_with_pickup(lanes):
    def handler(req):
        if req.url.path == "/v1/portal/ports":
            return httpx.Response(200, json=PORTS)
        return httpx.Response(200, json=rate_json(req.url.params["airline"], "330.00", transit=5))

    book = TariffBook([lanes[("Yellowtail Air Freight", "FRA-ORD")]])     # 80 kg -> 319.00, 4 days
    bid = candidate_from_bid("Orion Cargo Air", "FRA", "ORD", "325.00", transit_days=2)
    r = PriceRequest(destination="ORD", gross_kg=Decimal("80"), pickup_zone="60")

    async def run(pickup):
        async with client(handler) as c:
            return await build_candidates(r, book, c, pickup=pickup, bids=[bid], now=NOW, pivot_probes=0)

    cs = asyncio.run(run(None))
    assert cs.request.origin == "FRA"
    assert cs.eligible(NOW) == []                 # pickup required but unpriced -> nothing complete
    cs = asyncio.run(run(PickupQuote("60", "FRA", Decimal("40.00"))))
    best = cs.best(NOW)
    assert best.carrier == "Yellowtail Air Freight" and best.total == Decimal("359.00")    # 319 + 40
    assert "cheapest" in best.tags
    fastest = rank(cs.eligible(NOW), "fastest")[0]
    assert fastest.carrier == "Orion Cargo Air" and fastest.total == Decimal("365.00")
    assert all([x.code for x in c.charges].count("pickup") == 1 for c in cs.candidates)
    json.dumps(cs.to_dict(NOW))                  # serialisable for T3
