# Local sandbox

A separate, enforced environment for proving the whole workflow without any live effect. It has
its own compose project (`fiveu-sandbox`), database (`127.0.0.1:5434`), volume and settings.
It never reads `.env`.

Everything it produces is **simulated**. The mock customers follow local rules written in
`backend/app/sandbox/scenarios.py`. They are not the organiser's evaluator, and no result from
the sandbox is a competition score.

## Commands

```sh
make sandbox-up                                   # database, API (8100) and worker; waits for healthy
make sandbox-check                                # fails unless sandbox mode and the network guard are on
make simulate SCENARIO=happy_path SEED=42         # one scenario
make simulate SCENARIO=dg_missing_docs SEED=42
make simulate SCENARIO=expired_backlog SEED=42
make simulate SCENARIO=lost_ack SEED=42
make simulate SCENARIO=all SEED=42                # all scenarios, report in reports/
make verify                                       # lint, full test suite, all scenarios, frontend build
make sandbox-ui                                   # console at http://127.0.0.1:5174/sandbox
make sandbox-down                                 # stop; the database volume is kept
```

`make simulate` and `make verify` need `make sandbox-up` first (they use the sandbox database).

| What | URL |
| --- | --- |
| Sandbox console | http://127.0.0.1:5174/sandbox |
| Sessions board on sandbox data | http://127.0.0.1:5174/ |
| Sandbox API status | http://127.0.0.1:8100/sandbox/status |
| Sandbox database | `127.0.0.1:5434`, database `fiveu_sandbox` |
| Report | `reports/sandbox-report.md`, `reports/sandbox-results.json` (git-ignored) |

### Reset

Normal shutdown keeps all data. To delete **only** the sandbox database volume
(`fiveu-sandbox_fiveu_sandbox_pgdata`), run this yourself:

```sh
make sandbox-reset CONFIRM=sandbox
```

It cannot touch the demo stack (`compose.yaml`, port 5433) or anything live.

## How the sandbox is enforced

| Layer | What it does | Where |
| --- | --- | --- |
| Settings | `APP_MODE=sandbox` refuses to start unless live actions are off, `LLM_MODE=fake`, every API base URL is `sandbox.invalid` or loopback, and every key starts with `sandbox-` | `backend/app/core/config.py` |
| No `.env` | In sandbox mode settings come from the process environment only | `get_settings()` |
| Adapters | Without an installed simulator, `get_adapters()` raises `sandbox_adapters_missing` instead of falling back | `backend/app/platform/__init__.py` |
| In-memory transport | The real mailbox, booking, portal and gateway client code runs against `httpx.MockTransport`; no socket exists | `backend/app/sandbox/world.py` |
| Socket guard | The API, the worker and the CLI patch `socket` at start so only loopback and the database host can be reached; it is installed again on every restart | `backend/app/core/netguard.py` |
| Docker network | The worker and database are on an `internal` network with no route out | `compose.sandbox.yaml` |
| Console | `/sandbox/*` routes are registered only in sandbox mode (404 otherwise) | `backend/app/main.py` |

Known limit: the API container also sits on a normal network, because Docker cannot publish a
port from an internal one. For the API, the socket guard is the egress control, not the network.

## What the simulator provides

- **Mailbox**: ids, threads, `references`, attachments, paging, duplicate and delayed delivery,
  captured outbound mail, lost acknowledgements. Outbound mail never shows up in a poll.
- **Customers**: accept only if the quote arrives before the six-minute deadline, matches the
  request, is in the right currency, is within budget, and (for dangerous goods) the documents
  were asked for first. Otherwise they reject with a stated reason. They answer only the
  questions they are asked.
- **Rate portal and bookings**: several carriers per lane, USD rates with and without FX,
  short-lived rates, 429, 503, timeout and malformed answers, bookings keyed by `team_reference`.
- **Model gateway**: fixture answers, invalid JSON and timeouts, usage labelled synthetic.
- **Runner**: seeded randomness and an injected clock. The same scenario and seed give the same
  `digest`, on any machine and database.

API paths and field names follow `docs/contracts.md`. Anything the real API does beyond that
table is unverified.

## What changed in the application

- Plain-text RFQs are read by rules (`workflows/extract.py`); a model may only fill missing
  fields and its answer is validated.
- Missing route or weight, missing dangerous-goods documents, and "non-dangerous" next to a UN
  number each produce one clarification in the customer's thread, a `clarifying` stage and no
  price. Only `commit_first_quote` states a price.
- Quote, booking and tracking mails are built from recorded facts (`workflows/replies.py`) and
  sent through the mailbox adapter. `LiveMailbox.send` and `PortalBooking` are written from the
  documented DTOs; neither has been run against the live API.
- A send is committed as `sending` before the call. A lost acknowledgement or a crash leaves it
  `UNKNOWN`; it is never resent automatically.
- A booking is `submitted` before the portal call and `confirmed` only from a portal answer or
  a carrier confirmation mail. An unclear outcome is retried with the identical
  `rate_id`/`team_reference`.
- A job left `claimed` by a dead worker is taken back after 60 s; failed jobs back off.
- One malformed portal answer no longer aborts pricing for the whole request.
- Selection prefers offers that have a portal `rate_id`; a tariff-only offer is used only when
  nothing bookable exists, and its acceptance waits as `manual_booking_required`.

## Evidence

Statuses: PASS, FAIL, NOT IMPLEMENTED, LIVE UNVERIFIED. "Tested locally" means a sandbox
scenario (`backend/app/sandbox/scenarios.py`) or a test under `backend/tests/`.
**Live integration is unverified for every row**: nothing here has run against the 5U API.

| Task | Criterion (from the issue) | Implemented in | Local evidence | Result | Gap |
| --- | --- | --- | --- | --- | --- |
| T1 | Restart keeps cursors, work, evidence | `db/models.py`, compose volume | `sandbox-up` after `sandbox-down`; cursor continues across runs | PASS | none known |
| T1 | Paging and duplicates lose nothing, duplicate nothing | `platform/mailbox.py` | `paging_250`, `duplicates` | PASS | client pages at 100, API allows 200 |
| T1 | Two workers cannot make two first quotes | unique index, `workflows/rfq.py` | `two_workers` | PASS | |
| T1 | Paused or expired work starts no quote | `platform/outbox.py`, `workflows/rfq.py` | `pause_takeover`, `expired_backlog` | PASS | |
| T1 | Lost acknowledgement stays UNKNOWN, no resend | `platform/outbox.py` | `lost_ack`, `crash_recovery` | PASS | no operator action to resolve UNKNOWN |
| T1 | Dead worker's job is recovered | `db/repositories.py` | `test_a_job_left_claimed_by_a_dead_worker_is_taken_back` | PASS | |
| T1 | Routing by sender, thread and evidence | `platform/routing.py` | `tests/router/` | PASS | |
| T1 | Gateway usage with cost kind | `platform/gateway.py` | usage rows written, `cost_kind=unknown` | NOT IMPLEMENTED | no cost ledger or hourly accounting |
| T1 | Team-wide send throttling | | | NOT IMPLEMENTED | |
| T2 | Every tariff file approved or a visible exception | `pricing/parsers/` | `tests/pricing/test_ingest.py` | PASS | needs the `parsers` dependency group; CI skips it |
| T2 | Independent price fixtures | `pricing/engine.py` | `tests/sandbox/test_pricing_oracle.py` (hand-worked from the source tariff) | PASS | |
| T2 | Missing charge, currency, validity or FX blocks completeness | `pricing/contract.py` | `test_pricing_oracle.py`, `usd_lane`, `pickup_no_trucker` | PASS | |
| T2 | Portal faults do not stall other sources | `pricing/portal_client.py` | `portal_faults`, `slow_carrier` | PASS | malformed answer fixed in this change |
| T2 | Committed decisions keep their evidence | `quote_decisions.evidence` | `test_committed_quote_keeps_its_own_evidence` | PASS | |
| T2 | Detect changed tariffs or datasets, version new arrivals | | | NOT IMPLEMENTED | `tariff` mail has no handler |
| T3 | Extract and validate RFQ fields with evidence | `workflows/extract.py`, `rfq.py` | `happy_path`, `model_extraction`, `model_down`, extraction tests | PASS | rules cover IATA codes and kg only |
| T3 | Clarify missing information and documents | `workflows/rfq.py` | `missing_info`, `dg_missing_docs`, `dg_conflict`, `dg_conflict_cleared`, `dg_false_alarm` | PASS | |
| T3 | Cheapest and fastest; no transit cannot win fastest | `workflows/selection.py` | `fastest`, `test_fastest_never_picks_an_offer_without_transit_time` | PASS | |
| T3 | Contact two to five carriers, reconcile bids | | | NOT IMPLEMENTED | carrier bids are routed and ignored |
| T3 | One first quote; late bids and duplicates change nothing | `workflows/rfq.py` | `duplicates`, `late_bid` | PASS | `late_bid` passes because bids are not consumed |
| T3 | Acceptance matched to the stored offer; booking retried with the same identity | `workflows/booking.py` | `duplicates`, `booking_retry` | PASS | |
| T3 | Expired or changed offers are escalated | `workflows/booking.py` | `rate_expired_at_booking` | PASS | the customer is not told; no refresh |
| T3 | Pickup priced from a trucker | | `pickup_no_trucker` shows the gap | NOT IMPLEMENTED | door pickup always ends without an offer |
| T3 | Book offers that have no portal rate (email booking) | | `unbookable_only` shows the gap | NOT IMPLEMENTED | waits as `manual_booking_required` |
| T4 | Journey can be reconstructed | session events | `events` in every scenario result | PASS | |
| T4 | Tracking never invents a status | `workflows/followup.py`, `replies.py` | `happy_path`, `test_tracking_answer_uses_only_the_recorded_update` | PASS | status text comes from the first line of the tracking mail |
| T4 | Duplicate or unmatched updates | `workflows/followup.py` | `expired_backlog` (unmatched mail) | PASS | unmatched mail is flagged on the message, not shown in the UI |
| T4 | After pause, no new action starts | `platform/outbox.py`, `booking.py` | `pause_takeover` | PASS | |
| T4 | Live Sessions UI and sandbox console | `frontend/` | browser run of `dg_missing_docs` | PASS | |
| T4 | SSE stream with reconnect | | | NOT IMPLEMENTED | the board polls every 2 s |
| T4 | Authenticated operator commands | | | NOT IMPLEMENTED | no auth; localhost only |
| T5 | One replay command with controlled time | `sandbox/runner.py` | `make simulate` | PASS | |
| T5 | Faults: 429, 503, backlog, duplicate, restart, lost acknowledgement | `sandbox/world.py` | scenarios above | PASS | |
| T5 | Report with real outcomes | `sandbox/runner.py` | `reports/sandbox-report.md` | PASS | |
| T5 | Deployment and live smoke test | | | NOT IMPLEMENTED | not authorised |

## Not verified

- Any call to the real 5U API: mailbox send, bookings, rate portal, gateway.
- Writing quality of a real model. The sandbox uses templates; samples are in the report for a
  person to review.
- The organiser's acceptance rules, the sourcing rule and the scoring.
