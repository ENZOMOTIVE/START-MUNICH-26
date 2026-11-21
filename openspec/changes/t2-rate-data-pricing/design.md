# T2 — Technical design

## Module layout
```
pricing/
  __init__.py
  models.py          # RateCandidate, ChargeLine, ReviewException, Evidence (dataclasses)
  money.py           # Decimal helpers: quantize, rounding mode, FX apply
  tariffs/
    base.py          # Parser protocol + registry
    txt.py xlsx.py pdf.py docx.py ocr.py   # one reader per format
    ingest.py        # walk starter-kit/, checksum, version, emit records or exceptions
    store.py         # versioned tariff store (SQLite/JSON), dataset_version
  portal.py          # /v1/portal ports, brackets, rates; 429/503 backoff; evidence
  engine.py          # chargeable weight, weight-break pivot, min charge, surcharges, pickup leg
  pickup.py          # zone(2-digit) -> airport via pickup_zones.csv; truck leg hook
  candidates.py      # merge tariff + portal -> eligible RateCandidate list for T3
tests/pricing/
  fixtures/          # hand-checked expected totals
  test_engine.py test_ingest.py test_portal.py
```

## Key decisions
- **All money is `Decimal`.** No float anywhere in pricing (floats are refused).
  Rounding: ROUND_HALF_UP to cents per charge line, total = sum of rounded lines.
  Changed from "one quantize at the end" after live evidence: the portal returns
  372.12 for 100.3 kg at 3.71/kg all-in (exact 372.113), i.e. per-line rounding.
  `rounding="total"` remains available.
- **Chargeable weight** = `max(gross_kg, volume_m3 * 167)`. Portal applies its
  own; sheets apply each tariff's stated divisor.
- **Weight-break pivot.** Compute the total for the applicable bracket *and*
  every higher bracket (paying the higher break's minimum weight at its lower
  per-kg rate), then take the minimum. Minimum charge applies before surcharges.
- **Surcharges** (fuel + security) are per-kg on billed weight unless the sheet
  says otherwise; captured as separate `ChargeLine`s for evidence.
- **Pickup added exactly once**, only when the RFQ needs a door pickup; mapped
  from the 2-digit zone to a German airport.
- **Portal total already includes air + fuel + security**; do not re-add those.
  Pickup is still separate. FX field = EUR per unit of the returned currency.
- **Evidence over guesses.** Every record carries source file, checksum,
  effective date and the fields it was derived from. Unreadable -> ReviewException.
- **Expiry honoured.** A portal rate past `valid_until` is never presented as
  bookable.

## RateCandidate (output to T3)
`carrier, lane(origin,dest), service(cheapest/fastest/transit_days), total,
currency, charges[ChargeLine], chargeable_weight_kg, rate_id|null,
valid_until|null, dataset_version, source_links[], evidence{}`.
Implemented in `pricing/models.py` (see spec "RateCandidate fields").

## Pickup leg
No tariff prices trucking. The truck cost comes from a trucker bid / human as a
`PickupQuote(zone, airport, cost, currency, fx)`. An RFQ with a pickup zone but no
`PickupQuote` yields `complete=False, missing=["pickup"]` (never an invented number).

## Reuse
Port and harden the existing parsers/engine from the earlier prototype
(`app/tariffs/*`, `app/pricing.py`, `app/refdata.py`, `app/portal_check.py`).

## Update (21 Nov, aligned to team scaffold)

Canonical home is now `backend/app/pricing/` (per `docs/tasks/T2.md`); root `pricing/` and `tests/pricing/`
were removed. Output is the team contract `app.contracts.models.RateCandidate` / `ChargeLine` (money as
strings); the old `eligible/bookable/lane/dataset_version` fields are internal only (`PricedOption`) and are
mapped in `contract.py` (`dataset_version -> source_version`, incomplete -> `complete: false`).
Demo prefers fixtures (`fixtures/rates/demo-fra-lax.json`, Yellowtail 412.50). The full tariff parser lives
in `backend/app/pricing/parsers/` as a follow-up, not on the acceptance path.
