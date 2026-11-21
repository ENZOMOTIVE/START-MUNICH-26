# FREE CANDY — Air-Freight Forwarder Agent · Context Handoff

A multi-agent system that reads inbound air-freight RFQ emails and replies with an
all-in EUR quote, then handles DG docs, bookings and tracking. Built for the START
Hack Munich 2026 / 5U "digital air forwarder" challenge (team FREE CANDY).

## What it does
- Reads a mailbox for RFQs → classifies → extracts lane/weight/volume.
- Prices: chargeable_kg = max(gross, volume_cbm × 167); weight-break pivot; min charge;
  surcharges = (fuel+security) × billed kg; truck pickup leg via zone→airport.
- Composes a human-voice quote (the LLM never writes the numbers — a guarded price
  block is inserted; LLM output rejected if it changes a number).
- Screens dangerous goods (asks for DG docs), books, answers tracking.

## Architecture (concurrent)
Supervisor runs each inbound mail as its own task (up to 8), per-thread lock. Agents:
Intake (rule-based ~1ms) → Compliance (DG screen) → RefData (zone→airport, airlines,
weight brackets) + Pricing (tariff sheets + portal, read-budget gated) run in PARALLEL
→ Composer+LLM (exactly one LLM call/quote) → Carrier (email bids) → Booking (confirm/track).

## Benchmark (local sandbox, latest)
- fast: 780/780, 0.00% price deviation, 7/7 exact, avg 0.38s reply
- burst (10 reads/min cap): 780/780, 0.00%, avg 2.6s
- stress (12 scenarios): 1220/1220, 0.00%, 11/11 exact, all booked
- starved (portal forced off → email bids): all accepted/booked, mean 0.048% / max 0.53%
- 71/71 tests pass. 100% accept rate everywhere.

## Setup
```
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
make tariffs        # parse start-kit sheets -> data/tariffs.json
make test           # 71 tests
```

## Run modes
| Command | What |
|---|---|
| `make sandbox-e2e` | full local sim, asserts board (~45s) |
| `make sandbox-e2e-realistic` | live-event pacing, RFQ every 30s (~5 min) |
| `make sandbox-e2e-stress` / `-burst` / `-starved` | stress batteries |
| `make dashboard-lab` | all-in-one control room → http://127.0.0.1:8090 |
| `make simulate` | offline: prints what WOULD be sent |
| `make portal-check` | live READ-ONLY portal check (~5 of 10 reads/min) |
| `make run` | poll loop, DRY_RUN=true (logs, sends nothing) |
| `make run-live` | **REAL sends + bookings — team decision only** |

## Going from TEST (sandbox) to LIVE data
The only difference is a handful of environment variables. Nothing in code changes.

### 1. TEST / sandbox (default, safe, no secrets)
Points at a local mock on localhost; never touches the real API/mailbox.
```
HACK_BASE_URL=http://localhost:8080
HACK_API_KEY=sandbox          # dummy, any value
DRY_RUN=false                 # sends into the mock only
USE_LLM=true
LLM_ALLOW_REMOTE=false        # LLM stays on the local echo gateway (no cost)
```

### 2. LIVE / real event
```
HACK_BASE_URL=https://api.start-hack.5u.ai
HACK_API_KEY=<real 5U team key>      # see Credentials below — NEVER commit
HACK_MAILBOX=free-candy@hackloop.mail
DRY_RUN=false                        # <-- real sends + bookings
USE_LLM=true                         # optional; real email polishing
LLM_ALLOW_REMOTE=true                # allow the real 5U gateway (costs money)
TRUCK_CALIBRATE=true                 # optional: emails ~34 truckers at startup for real pickup rates
```
Then: `make run-live`  (or `DRY_RUN=false python -m app.main`)

Pre-flight before going live:
- `make portal-check` (read-only) to confirm the key + lane data work.
- Start DRY_RUN=true (`make run`) first and watch the dashboard, then flip DRY_RUN=false.

## Credentials (set as env vars or in `.env` — do NOT hardcode in source)
`.env` is gitignored; keep it that way.
```
HACK_API_KEY=__5U_TEAM_API_KEY__      # the real 5U/START Hack team key
HACK_MAILBOX=free-candy@hackloop.mail
# Optional, only if USE_LLM + LLM_ALLOW_REMOTE for the real gateway:
OPENAI_API_KEY=__OPENAI_KEY__         # pull from vault; rotate the one shared in chat
```
Real secret values are kept in Samer's vault / the box `.env`, not in this file.
Ask Samer (or read `.env`) for the actual `HACK_API_KEY`. The OpenAI key shared over
chat should be rotated before reuse.

## Key config knobs (app/config.py, all env-overridable)
- `PORTAL_MAX_WAIT_S=30` — max wait for portal read budget before quoting with what's priced
- `POLL_INTERVAL_S=0.2`, `POLL_MAX_INFLIGHT=3` — mailbox polling cadence
- `DG_REPLY_MODE=quote_and_docs` — DG: price + docs request in first mail
- `TRUCK_CALIBRATE=false` — real trucker price emails at startup (live only)
- `VOLUMETRIC_KG_PER_CBM=167` — volumetric divisor
- `PRICING_PIVOT=true` — weight-break pivot on

## Layout
- `app/orchestrator.py` — supervisor
- `app/agents/` — intake, compliance, refdata, pricing, composer, llm, carrier, booking, truck
- `app/sandbox/` — local mock API + scorer + e2e + scenarios
- `dashboard/` — local control-room UI (server.py, lab.py, static/index.html)
- `tests/` — 71 tests
- `data/` — tariffs.json + benchmark reports

## Safety invariants
- Everything local by default; sandbox only talks to 127.0.0.1.
- One LLM call per quote; LLM never emits the price; guard falls back to template.
- DG first reply always requests DG documents.
- `.env` never committed; no secrets in code or this doc.
