"""5U rate portal client: /v1/portal/ports, /brackets, /rates (READ ONLY - this module never books).

* Rate reads are capped per key per minute (X-RateLimit-Limit, currently 10). We track
  X-RateLimit-Remaining and Retry-After; 429/503 are retried a bounded number of times with a
  bounded wait.
* Every lane/airline call runs under its own timeout; one slow or limited source never stalls the
others,
  and its failure is recorded (PortalFailure) instead of raised.
* The portal `total` already includes freight + fuel + security. Pickup is NOT included.
* `fx_eur_per_usd` (despite the name) = EUR per 1 unit of the returned `currency`.
* A rate past `valid_until` is never presented as bookable.

    python -m app.pricing.portal_client ports | brackets <airline> | rate <ORIG> <DEST> <airline>
    <kg> [m3]
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal

from . import settings as config
from .money import D, MoneyError, chargeable_weight, fx_to_eur, q2
from .quote_models import Evidence, PricedCharge, PricedOption

log = logging.getLogger("app.pricing.portal_client")


@dataclass
class PortalFailure:
    origin: str | None
    destination: str | None
    airline: str | None
    kind: str  # rate_limited | unavailable | timeout | http_error | not_on_portal | bad_response
    detail: str

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class PortalError(Exception):
    def __init__(self, kind: str, detail: str, retry_after: float | None = None):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail, self.retry_after = kind, detail, retry_after


def parse_ts(s: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class PortalClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout_s: float = 8.0,
        max_retries: int = 2,
        max_wait_s: float = 8.0,
        cache_ttl_s: float = 300.0,
        http=None,
    ):
        if http is None:
            import httpx

            if base_url is None or api_key is None:
                b, k = config.api_settings()
                base_url, api_key = base_url or b, api_key or k
            http = httpx.AsyncClient(
                base_url=base_url, headers={"X-API-Key": api_key}, timeout=timeout_s
            )
        self.http = http
        self.timeout_s, self.max_retries, self.max_wait_s, self.cache_ttl_s = (
            timeout_s,
            max_retries,
            max_wait_s,
            cache_ttl_s,
        )
        self.rate_limit: int | None = None
        self.rate_remaining: int | None = None
        self._window_reset: float = 0.0
        self._ports: tuple[float, dict] | None = None
        self._brackets: dict[str, tuple[float, list[int] | None]] = {}

    async def aclose(self) -> None:
        await self.http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        await self.aclose()

    # ---------- low level ----------
    async def _get(self, path: str, params: dict | None = None, counted: bool = False):
        attempt = 0
        while True:
            if counted and time.monotonic() < self._window_reset:  # set only by a 429 Retry-After
                wait = self._window_reset - time.monotonic()
                if wait > self.max_wait_s:
                    raise PortalError(
                        "rate_limited", f"budget exhausted, next window in {wait:.0f}s", wait
                    )
                await asyncio.sleep(wait)
            try:
                r = await asyncio.wait_for(self.http.get(path, params=params), self.timeout_s)
            except TimeoutError:
                raise PortalError("timeout", f"{path} > {self.timeout_s}s")
            except Exception as e:  # network error
                if attempt < self.max_retries:
                    attempt += 1
                    await asyncio.sleep(0.5 * attempt)
                    continue
                raise PortalError("http_error", f"{type(e).__name__}: {e}")
            if counted:
                lim, rem = (
                    r.headers.get("X-RateLimit-Limit"),
                    r.headers.get("X-RateLimit-Remaining"),
                )
                if lim and lim.isdigit():
                    self.rate_limit = int(lim)
                if rem and rem.isdigit():
                    self.rate_remaining = int(rem)
            if r.status_code in (429, 503):
                ra = r.headers.get("Retry-After")
                try:
                    wait = float(ra) if ra else 1.0 * (attempt + 1)
                except ValueError:
                    wait = 1.0 * (attempt + 1)
                kind = "rate_limited" if r.status_code == 429 else "unavailable"
                if r.status_code == 429:
                    self.rate_remaining = 0
                    self._window_reset = time.monotonic() + wait
                if attempt < self.max_retries and wait <= self.max_wait_s:
                    attempt += 1
                    await asyncio.sleep(wait)
                    continue
                raise PortalError(kind, f"HTTP {r.status_code}: {r.text[:200]}", wait)
            return r

    # ---------- endpoints ----------
    async def ports(self, force: bool = False) -> dict:
        if self._ports and not force and time.monotonic() - self._ports[0] < self.cache_ttl_s:
            return self._ports[1]
        r = await self._get("/v1/portal/ports")
        if r.status_code != 200:
            raise PortalError("http_error", f"ports HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        self._ports = (time.monotonic(), data)
        return data

    async def airlines_on(self, origin: str, dest: str) -> list[dict]:
        data = await self.ports()
        for ln in data.get("lanes", []):
            if ln["origin"] == origin and ln["destination"] == dest:
                return ln["airlines"]
        return []

    async def brackets(self, airline: str) -> list[int] | None:
        """Sorted weight breaks, or None if the airline is not on the portal (404 -> price by
        email)."""
        hit = self._brackets.get(airline)
        if hit and time.monotonic() - hit[0] < self.cache_ttl_s:
            return hit[1]
        r = await self._get("/v1/portal/brackets", {"airline": airline})
        if r.status_code == 404:
            val = None
        elif r.status_code == 200:
            val = sorted(int(b) for b in r.json()["breaks_kg"])
        else:
            raise PortalError("http_error", f"brackets HTTP {r.status_code}: {r.text[:200]}")
        self._brackets[airline] = (time.monotonic(), val)
        return val

    async def rate(self, origin: str, dest: str, airline: str, weight_kg, volume_m3=0) -> dict:
        params = {
            "origin": origin,
            "destination": dest,
            "airline": airline,
            "weight_kg": str(D(weight_kg)),
            "volume_cbm": str(D(volume_m3 or 0)),
        }
        r = await self._get("/v1/portal/rates", params, counted=True)
        if r.status_code == 404:
            raise PortalError("not_on_portal", r.text[:300])
        if r.status_code != 200:
            raise PortalError("http_error", f"rates HTTP {r.status_code}: {r.text[:200]}")
        data = r.json()
        data["_fetched_at"] = now_utc().isoformat()
        return data


def candidate_from_rate(
    r: dict, gross_kg, volume_m3=0, requested_kg=None, now: dt.datetime | None = None
) -> PricedOption:
    """Portal Rate JSON -> PricedOption. Total = portal total (already air+fuel+security), FX'd to
    EUR."""
    now = now or now_utc()
    missing: list[str] = []
    for k in ("rate_id", "total", "currency", "valid_until", "dataset_version"):
        if not r.get(k):
            missing.append(k)
    cur = (r.get("currency") or "").upper()
    total = D(str(r["total"])) if r.get("total") else None
    fx = D(str(r["fx_eur_per_usd"])) if r.get("fx_eur_per_usd") else None
    air_eur = None
    if total is not None and cur:
        try:
            air_eur = fx_to_eur(total, cur, fx)
        except MoneyError:
            missing.append(f"fx:{cur}")
    vu = parse_ts(r["valid_until"]) if r.get("valid_until") else None
    ch_portal = D(str(r["chargeable_weight_kg"])) if r.get("chargeable_weight_kg") else None
    ch_ours = chargeable_weight(gross_kg, volume_m3)
    billed = ch_portal if ch_portal is not None else ch_ours
    ev = Evidence(
        source_file="GET /v1/portal/rates",
        locator=r.get("rate_id"),
        method="portal",
        raw=json.dumps({k: v for k, v in r.items() if not k.startswith("_")}, sort_keys=True),
    )
    charges = []
    if air_eur is not None:
        desc = f"Air freight all-in (freight+fuel+security) {r.get('airline')} portal rate"
        if cur != "EUR":
            desc += f" {total} {cur} x {fx} EUR/{cur}"
        charges.append(PricedCharge("air_all_in", desc, air_eur, "EUR", billed, evidence=ev))
    pivoted = requested_kg is not None and D(requested_kg) > ch_ours
    c = PricedOption(
        carrier=r.get("airline", "?"),
        carrier_code=r.get("airline_code"),
        origin=r.get("origin"),
        destination=r.get("destination"),
        source="portal",
        total=None,
        charges=charges,
        chargeable_weight_kg=ch_ours,
        billed_weight_kg=billed,
        transit_days=r.get("transit_days"),
        rate_id=r.get("rate_id"),
        valid_until=vu,
        dataset_version=r.get("dataset_version"),
        source_links=[f"portal:rate:{r.get('rate_id')}"],
        source_total=total,
        source_currency=cur or None,
        fx_eur_per_unit=fx,
        air_total=q2(air_eur) if air_eur is not None else None,
        pivoted=pivoted,
        missing=missing,
        evidence={
            "portal_response": {k: v for k, v in r.items() if not k.startswith("_")},
            "fetched_at": r.get("_fetched_at"),
            "requested_weight_kg": str(D(requested_kg))
            if requested_kg is not None
            else str(D(gross_kg)),
            "includes": ["air_freight", "fuel", "security"],
            "excludes": ["pickup"],
        },
    )
    if ch_portal is not None and not pivoted and ch_portal != ch_ours:
        c.evidence["chargeable_mismatch"] = f"portal {ch_portal} vs ours {ch_ours}"
    if vu is not None and vu <= now:
        c.missing.append("expired")
    return c


@dataclass
class PortalQuotes:
    candidates: list[PricedOption] = field(default_factory=list)
    failures: list[PortalFailure] = field(default_factory=list)
    calls: int = 0


async def portal_quotes(
    client: PortalClient,
    origin: str,
    dest: str,
    gross_kg,
    volume_m3=0,
    *,
    pivot_probes: int = 1,
    max_pivot_ratio: Decimal = Decimal("1.6"),
    lane_timeout_s: float = 20.0,
    now: dt.datetime | None = None,
) -> PortalQuotes:
    """All portal airlines on a lane, concurrently. Optionally probe the next break(s) for a pivot.

    A pivot can only win if break/chargeable < rate(current)/rate(next); real break ratios stay
    under ~1.6,
    so higher breaks further away than that are not probed (saves the 10/min rate budget).
    """
    out = PortalQuotes()
    try:
        airlines = await client.airlines_on(origin, dest)
    except PortalError as e:
        out.failures.append(PortalFailure(origin, dest, None, e.kind, e.detail))
        return out
    ch = chargeable_weight(gross_kg, volume_m3)

    async def one(a: dict) -> PricedOption | None:
        code = a["code"]
        try:
            r = await client.rate(origin, dest, code, gross_kg, volume_m3)
            out.calls += 1
            best = candidate_from_rate(r, gross_kg, volume_m3, now=now)
            if pivot_probes > 0:
                brks = await client.brackets(code) or []
                higher = [b for b in brks if b > ch and Decimal(b) <= ch * max_pivot_ratio][
                    :pivot_probes
                ]
                for b in higher:
                    try:
                        rp = await client.rate(origin, dest, code, b, 0)
                        out.calls += 1
                    except PortalError as e:
                        out.failures.append(
                            PortalFailure(
                                origin, dest, code, e.kind, f"pivot probe {b} kg: {e.detail}"
                            )
                        )
                        continue
                    cand = candidate_from_rate(rp, gross_kg, volume_m3, requested_kg=b, now=now)
                    cand.break_kg = b
                    if (
                        cand.air_total is not None
                        and best.air_total is not None
                        and cand.air_total < best.air_total
                    ):
                        cand.evidence["pivot_vs"] = {
                            "rate_id": best.rate_id,
                            "air_total": str(best.air_total),
                        }
                        best = cand
            return best
        except PortalError as e:
            out.failures.append(PortalFailure(origin, dest, code, e.kind, e.detail))
            return None

    async def guarded(a):
        try:
            return await asyncio.wait_for(one(a), lane_timeout_s)
        except TimeoutError:
            out.failures.append(
                PortalFailure(origin, dest, a["code"], "timeout", f"> {lane_timeout_s}s")
            )
            return None

    res = await asyncio.gather(*(guarded(a) for a in airlines))
    out.candidates = [c for c in res if c is not None]
    return out


async def _cli(argv: list[str]) -> int:
    async with PortalClient() as c:
        cmd = argv[0] if argv else "ports"
        if cmd == "ports":
            d = await c.ports(force=True)
            print(json.dumps(d, indent=1))
            print(f"# {len(d['lanes'])} lanes", file=sys.stderr)
        elif cmd == "brackets":
            print(await c.brackets(argv[1]))
        elif cmd == "rate":
            o, de, a, kg = argv[1:5]
            m3 = argv[5] if len(argv) > 5 else "0"
            r = await c.rate(o.upper(), de.upper(), a, kg, m3)
            print(json.dumps(candidate_from_rate(r, D(kg), D(m3)).to_dict(now_utc()), indent=1))
            print(
                f"# rate budget left this minute: {c.rate_remaining}/{c.rate_limit}",
                file=sys.stderr,
            )
        else:
            print(__doc__)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli(sys.argv[1:])))
