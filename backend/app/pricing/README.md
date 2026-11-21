# app/pricing — T2 rate data & pricing

Output contract: `app.contracts.models.RateCandidate` + `ChargeLine` (money as strings, ISO-8601
`valid_until`). Deterministic Decimal code only — **no LLM / gateway call anywhere** (cost points).

## T2 acceptance path (fixtures first)

```
RFQ ─▶ candidates.build_rate_candidates()
        ├─ live actions OFF (APP_MODE=demo / LIVE_ACTIONS_ENABLED=false)
        │     └─ fixtures.candidates_from_fixtures()  ← FIXTURES_DIR/rates/*.json (lane match)
        └─ live actions ON
              └─ live_candidates_async(): portal_client (GET /v1/portal/rates, pivot probe)
                                          + engine.tariff_candidate() over data/tariff_snapshot.json
      ─▶ list[RateCandidate]  ─▶ calculator.select_offer()  (T3 picks; complete=False never selected)
```

`tariffs.load_fixture_candidates(db, rfq)` (scaffold name kept) persists them as `rate_candidates` rows
for `app.workflows.rfq`.

Demo RFQ FRA→LAX 250 kg, pickup zone 60 (→ FRA), cheapest ⇒ **Yellowtail `412.50` EUR**
(air_freight 350.00 + fuel 29.25 + security 3.50 + pickup 29.75), R-DEMOYELLOW01, transit 5.
Helix 520.00 (3 d), Vega 480.00 (4 d). Asserted in `backend/tests/pricing/test_t2_acceptance.py`.

A fixture/portal/tariff candidate is `complete: false` (kept, but never selectable) when: total ≠ sum of
charges, currency/charges missing, expired `valid_until`, pickup required but no single pickup line,
pickup zone maps to another airport, missing FX, or no real trucker price for a pickup leg.

## Files

| File | Role |
| --- | --- |
| `candidates.py` | entry point: fixtures first, live second |
| `fixtures.py` | fixture loader → `RateCandidate` (+ completeness checks) |
| `contract.py` | internal `PricedOption` → team `RateCandidate` mapping |
| `calculator.py` | `select_offer` (cheapest / fastest over complete candidates) |
| `tariffs.py` | DB glue (`load_fixture_candidates`) |
| `portal.py` | `FakePortal` / `LivePortal` adapters (GET rates only; booking is T3) + `rate_candidate()` |
| `portal_client.py` | async read-only portal client: retries on 429/503, per-lane isolation, pivot probe |
| `engine.py` | weight-break pricing: chargeable = max(kg, m³×167), pivot to higher breaks, minimum before surcharges, fuel+security per billed kg, per-line cent rounding |
| `pickup.py` | zone → airport (`starter-kit/pickup_zones.csv`, bundled copy in `data/` for the container), pickup leg added exactly once |
| `money.py` | strict Decimal helpers (floats refused), units, FX |
| `quote_models.py` | internal working types (`PricedOption`, `PricedCharge`, `Evidence`, `ReviewException`) |
| `tariff_candidates.py` | merges tariff + portal + bid options, ranking (live path) |
| `parsers/` | **follow-up, not T2** — full tariff parser (see `parsers/README.md`) |

## Run

```sh
cd backend
uv run pytest -q tests/pricing            # pricing unit + acceptance (DB tests need Postgres, as make test)
make test                                 # full suite (from repo root, uses compose Postgres)
T2_LIVE_TEST=1 uv run pytest -q tests/pricing -k live_portal_read_smoke   # 1 real portal GET (needs key)
```

The key comes from `HACKATHON_API_KEY` (team settings / `.env`, git-ignored). `HACK_API_KEY` is still
accepted as a fallback.
