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
| Docker network | The API and the worker are only on an `internal` network with no route out. Port 8100 is published by a forwarder container that holds no application code | `compose.sandbox.yaml` |
| Console | `/sandbox/*` routes are registered only in sandbox mode (404 otherwise) | `backend/app/main.py` |

The console can stop a run at a simulated second ("Stop at simulated second"), which is how you
step through a scenario: runs are deterministic, so a later stop shows the same run further on.

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
- Selection prefers offers that have a portal `rate_id`. A tariff-only offer is used only when
  nothing bookable exists; it is booked by a mail to the airline and stays `submitted` until
  the airline's "Booking <reference> confirmed" mail arrives.
- A door pickup asks up to three truckers that serve the zone and adds the first usable bid
  once. If nobody answers, the customer is told a minute before the deadline.
- Live sends are spaced to stay under 60 mails a minute; mail left `pending` is sent by a
  sweep, and a first quote is never sent after its request expired.
- Checked against the public API description on 2026-11-21: `MessageIn`, `BookingIn`,
  `BookingOut`, the 201/200 booking retry rule and the confirmation mail. Still never called.

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
| T1 | Gateway usage with cost kind | `platform/gateway.py`, `GET /api/usage` | `test_usage_reports_cost_as_unknown_until_it_is_known` | PASS | calls are counted; cost stays `unknown` because no price list is verified; no hourly accounting |
| T1 | Send throttling under the 60 mails/minute limit | `platform/outbox.py` | `test_live_sends_are_spaced_under_the_mailbox_limit` | PASS | not locked across several workers |
| T1 | Unsent mail is retried, never after the deadline | `platform/outbox.py` (`deliver_pending`) | same test; `expired_backlog` | PASS | |
| T2 | Every tariff file approved or a visible exception | `pricing/parsers/` | `tests/pricing/test_ingest.py` | PASS | needs the `parsers` dependency group; CI skips it |
| T2 | Independent price fixtures | `pricing/engine.py` | `tests/sandbox/test_pricing_oracle.py` (hand-worked from the source tariff) | PASS | |
| T2 | Missing charge, currency, validity or FX blocks completeness | `pricing/contract.py` | `test_pricing_oracle.py`, `usd_lane`, `pickup_no_trucker` | PASS | |
| T2 | Portal faults do not stall other sources | `pricing/portal_client.py` | `portal_faults`, `slow_carrier` | PASS | malformed answer fixed in this change |
| T2 | Committed decisions keep their evidence | `quote_decisions.evidence` | `test_committed_quote_keeps_its_own_evidence` | PASS | |
| T2 | Detect a changed or new tariff file | `workflows/followup.py` (`handle_tariff_mail`) | `tariff_update`, `test_an_approved_tariff_file_is_recognised` | PASS | the file is flagged for review in `GET /api/review`; it is not parsed or versioned automatically |
| T3 | Extract and validate RFQ fields with evidence | `workflows/extract.py`, `rfq.py` | `happy_path`, `model_extraction`, `model_down`, extraction tests | PASS | rules cover IATA codes and kg only |
| T3 | Clarify missing information and documents | `workflows/rfq.py` | `missing_info`, `dg_missing_docs`, `dg_conflict`, `dg_conflict_cleared`, `dg_false_alarm` | PASS | |
| T3 | Cheapest and fastest; no transit cannot win fastest | `workflows/selection.py` | `fastest`, `test_fastest_never_picks_an_offer_without_transit_time` | PASS | |
| T3 | Ask truckers for a door pickup, use the bid | `workflows/rfq.py`, `followup.py` | `pickup_with_trucker`, `pickup_no_reply`, `pickup_no_trucker` | PASS | up to three truckers; the first usable EUR bid is used, a later cheaper one is only recorded |
| T3 | Ask airlines for bids (two to five carriers) | | | NOT IMPLEMENTED | airline prices come from the portal and the approved tariffs; the organiser's sourcing rule is unconfirmed |
| T3 | One first quote; late bids and duplicates change nothing | `workflows/rfq.py` | `duplicates`, `late_bid` | PASS | `late_bid` passes because bids are not consumed |
| T3 | Acceptance matched to the stored offer; booking retried with the same identity | `workflows/booking.py` | `duplicates`, `booking_retry` | PASS | |
| T3 | Expired or changed offers are escalated | `workflows/booking.py` | `rate_expired_at_booking` | PASS | the customer is told nothing was booked; a new offer is not sent automatically |
| T3 | Book offers that have no portal rate, by mail | `workflows/booking.py` (`_book_by_mail`) | `email_booking`, `email_booking_silent` | PASS | the mail wording and the airline's reply format are assumptions |
| T4 | Journey can be reconstructed | session events | `events` in every scenario result | PASS | |
| T4 | Tracking never invents a status | `workflows/followup.py`, `replies.py` | `happy_path`, `test_tracking_answer_uses_only_the_recorded_update` | PASS | status text comes from the first line of the tracking mail |
| T4 | Duplicate or unmatched updates | `workflows/followup.py`, `GET /api/review` | `expired_backlog`, `test_review_list_shows_what_waits_for_a_person` | PASS | the review list is an API; the board does not show it yet |
| T4 | After pause, no new action starts | `platform/outbox.py`, `booking.py` | `pause_takeover` | PASS | |
| T4 | Live Sessions UI and sandbox console | `frontend/` | browser run of `dg_missing_docs` | PASS | |
| T4 | Event stream with reconnect | `GET /api/sessions/{id}/stream` | `test_event_stream_resumes_without_repeating` | PASS | the board still polls every 2 s; it does not use the stream yet |
| T4 | Operator commands need a token | `api/sessions.py` (`OPERATOR_TOKEN`) | `test_pause_needs_the_operator_token_when_one_is_set` | PASS | off unless `OPERATOR_TOKEN` is set; one shared token, no users |
| T5 | One replay command with controlled time | `sandbox/runner.py` | `make simulate` | PASS | |
| T5 | Faults: 429, 503, backlog, duplicate, restart, lost acknowledgement | `sandbox/world.py` | scenarios above | PASS | |
| T5 | Report with real outcomes | `sandbox/runner.py` | `reports/sandbox-report.md` | PASS | |
| T5 | Deployment and live smoke test | | | NOT IMPLEMENTED | not authorised |

## Not verified

- Any call to the real 5U API: mailbox send, bookings, rate portal, gateway.
- Writing quality of a real model. The sandbox uses templates; samples are in the report for a
  person to review.
- The organiser's acceptance rules, the sourcing rule and the scoring.
