"""Fixtures-first rate loader (the T2 demo path). Pure file reads: no DB, no network, no LLM.

Reads every `FIXTURES_DIR/rates/*.json` (see fixtures/rates/demo-fra-lax.json) and turns the candidates of
the file(s) whose `match` lane equals the RFQ lane into team `RateCandidate`s (simulated=True).

A fixture candidate is only `complete` if it passes the same checks a real quote must pass:
  * total, currency and >=1 charge line present, and total == sum(charges) to the cent
  * not expired (valid_until in the future)
  * if the RFQ needs a pickup: a `pickup` charge is present exactly once, and the pickup zone maps
    (starter-kit/pickup_zones.csv) to the RFQ origin airport
Anything else is kept but marked complete=False, so T3 cannot select it.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.contracts.models import ChargeLine, RateCandidate

from .contract import _get, charges_sum, parse_ts, rfq_id_of
from .money import D, MoneyError
from .pickup import airport_for_zone

log = logging.getLogger("app.pricing.fixtures")


@dataclass(frozen=True)
class RateFixture:
    path: Path
    source_ref: str
    source_version: str
    match: dict[str, Any]
    candidates: list[dict[str, Any]]

    def matches(self, origin: str, destination: str) -> bool:
        m = self.match
        if not m.get("origin") or not m.get("destination"):
            return False
        return (
            str(m["origin"]).upper() == str(origin).upper()
            and str(m["destination"]).upper() == str(destination).upper()
        )


def fixtures_root(fixtures_dir: str | Path | None = None) -> Path:
    if fixtures_dir:
        return Path(fixtures_dir)
    from app.core.config import get_settings

    return get_settings().fixtures_path()


def load_rate_fixtures(fixtures_dir: str | Path | None = None) -> list[RateFixture]:
    root = fixtures_root(fixtures_dir) / "rates"
    out: list[RateFixture] = []
    if not root.is_dir():
        return out
    for path in sorted(root.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("skipping unreadable rate fixture %s: %s", path, e)
            continue
        cands = data.get("candidates", data if isinstance(data, list) else [])
        match = data.get("match") or {}
        if not match and cands:
            # legacy fixture without a match block: derive the lane from the file name (demo-fra-lax)
            parts = path.stem.split("-")
            if len(parts) >= 3:
                match = {"origin": parts[-2].upper(), "destination": parts[-1].upper()}
        out.append(
            RateFixture(
                path=path,
                source_ref=data.get("source_ref", f"fixtures/rates/{path.name}"),
                source_version=data.get("source_version", "unknown"),
                match=match,
                candidates=list(cands),
            )
        )
    return out


def fixture_item_to_contract(
    item: dict[str, Any],
    fx: RateFixture,
    rfq_id: uuid.UUID,
    *,
    origin: str,
    pickup_required: bool,
    pickup_zone: str | None,
    now: dt.datetime,
) -> tuple[RateCandidate, list[str]]:
    problems: list[str] = []
    try:
        charges = [
            ChargeLine(
                code=str(c["code"]),
                amount=D(str(c["amount"])),
                currency=str(c.get("currency", item.get("currency", "EUR"))),
                unit=str(c.get("unit", "shipment")),
            )
            for c in item.get("charges") or []
        ]
    except (KeyError, MoneyError) as e:
        charges, problems = [], [f"bad_charge:{e}"]
    total_raw = item.get("total")
    try:
        total = D(str(total_raw)) if total_raw not in (None, "") else None
    except MoneyError:
        total = None
    if total is None:
        problems.append("total_missing")
    if not charges:
        problems.append("charges_missing")
    elif total is not None and charges_sum(charges) != total.quantize(Decimal("0.01")):
        problems.append(f"total_mismatch:{total}!={charges_sum(charges)}")
    currency = item.get("currency")
    if not currency:
        problems.append("currency_missing")
    elif any(c.currency != currency for c in charges):
        problems.append("mixed_currency")
    valid_until = parse_ts(item.get("valid_until"))
    if valid_until is not None and valid_until <= now:
        problems.append("expired")
    pickups = [c for c in charges if c.code == "pickup"]
    if pickup_required or pickup_zone:
        if len(pickups) != 1:
            problems.append(f"pickup_lines:{len(pickups)}")
        if pickup_zone:
            ap = airport_for_zone(pickup_zone)
            if ap is None:
                problems.append(f"pickup_zone_unknown:{pickup_zone}")
            elif ap != origin.upper():
                problems.append(f"pickup_airport_mismatch:{pickup_zone}->{ap}!={origin}")
    complete = bool(item.get("complete", True)) and not problems
    cand = RateCandidate(
        id=uuid.uuid4(),
        rfq_id=rfq_id,
        carrier=str(item["carrier"]),
        carrier_code=item.get("carrier_code"),
        total=total if total is not None else Decimal("0"),
        currency=str(currency or "EUR"),
        transit_days=item.get("transit_days"),
        rate_id=item.get("rate_id"),
        complete=complete,
        valid_until=valid_until,
        source_ref=str(item.get("source_ref", fx.source_ref)),
        source_version=str(item.get("source_version", fx.source_version)),
        charges=charges,
        simulated=True,
    )
    return cand, problems


def candidates_from_fixtures(
    rfq: Any, *, fixtures_dir: str | Path | None = None, now: dt.datetime | None = None
) -> list[RateCandidate]:
    """RFQ (contract RFQ, RfqRow or dict) -> RateCandidates from matching fixture files. [] if none match."""
    now = now or dt.datetime.now(dt.UTC)
    origin = str(_get(rfq, "origin", "")).upper()
    destination = str(_get(rfq, "destination", "")).upper()
    rfq_id = rfq_id_of(rfq)
    pickup_required = bool(_get(rfq, "pickup_required", False))
    pickup_zone = _get(rfq, "pickup_zone")
    out: list[RateCandidate] = []
    for fx in load_rate_fixtures(fixtures_dir):
        if not fx.matches(origin, destination):
            continue
        mw = fx.match.get("weight_kg")
        w = _get(rfq, "weight_kg")
        if mw is not None and w is not None and D(str(mw)) != D(str(w)):
            log.warning(
                "rate fixture %s is hand-checked for %s kg, RFQ asks %s kg (demo only)",
                fx.path.name,
                mw,
                w,
            )
        for item in fx.candidates:
            cand, problems = fixture_item_to_contract(
                item,
                fx,
                rfq_id,
                origin=origin,
                pickup_required=pickup_required,
                pickup_zone=pickup_zone,
                now=now,
            )
            if problems:
                log.info("fixture candidate %s incomplete: %s", cand.carrier, problems)
            out.append(cand)
    return out
