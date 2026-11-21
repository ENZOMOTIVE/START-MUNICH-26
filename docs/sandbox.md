# Local sandbox

A separate, enforced environment for proving the whole workflow without any live effect. It has
its own compose project (`fiveu-sandbox`), databases (`127.0.0.1:5434` and `5435`), volumes and
settings. It never reads `.env`.

Everything it produces is **simulated**. The mock customers follow local rules written in
`backend/app/sandbox/scenarios.py`. They are not the organiser's evaluator, and no result from
the sandbox is a competition score. Nothing in this document has run against the 5U API.

## Commands

```sh
make sandbox-up                            # database, API (8100) and worker; waits for healthy
make sandbox-check                         # fails unless sandbox mode and the network guard are on
make simulate SCENARIO=happy_path SEED=42  # one scenario
make simulate SCENARIO=all SEED=42         # all 37 scenarios, report in reports/
make verify                                # lint, full test suite, all scenarios, frontend build
make sandbox-ui                            # console at http://127.0.0.1:5174/sandbox
make sandbox-down                          # stop; the database volume is kept

make drill-up                              # real API (8102) and worker against the simulator container
make drill-check                           # container kills, one-worker check, denied network attempts
make drill-ui                              # operator UI at http://127.0.0.1:5175 (token: sandbox-operator)
make drill-down                            # stop the drill containers; their volume is kept
```

`make simulate` and `make verify` need `make sandbox-up` first (they use the sandbox database).
Run one test or scenario run at a time: a run holds the mailbox poller lock, and a second run,
or a suspended one, makes the other wait.

| What | Where |
| --- | --- |
| Sandbox console | http://127.0.0.1:5174/sandbox |
| Sandbox API status | http://127.0.0.1:8100/sandbox/status |
| Sandbox database | `127.0.0.1:5434`, database `fiveu_sandbox` |
| Drill operator UI, review queue | http://127.0.0.1:5175/, http://127.0.0.1:5175/review |
| Drill API, simulator state | http://127.0.0.1:8102, http://127.0.0.1:8101/_sim/state |
| Drill database | `127.0.0.1:5435`, database `fiveu_sandbox_drill` |
| Reports (git-ignored) | `reports/sandbox-report.md`, `reports/sandbox-results.json`, `reports/drill.json`, `reports/browser/` |

### Reset

Normal shutdown keeps all data. To delete **only** the sandbox database volume
(`fiveu-sandbox_fiveu_sandbox_pgdata`), run this yourself:

```sh
make sandbox-reset CONFIRM=sandbox
```

It cannot touch the demo stack (`compose.yaml`, port 5433) or anything live.

## The three ways the application runs without live effect

| Mode | Reads | Writes | Use |
| --- | --- | --- | --- |
| `sandbox`, in memory | simulator inside the process, no socket | simulator only | `make simulate`, tests: fast, deterministic, simulated clock |
| `sandbox`, drill | simulator in its own container, real HTTP on an internal network | simulator only | real processes and real time: container kills, the operator UI |
| `observe` | the **real** mailbox, GET only | nothing | first look at live mail; every proposed action is recorded as `observed` and can never be sent |

## How it is enforced

| Layer | What it does | Where |
| --- | --- | --- |
| Settings | `APP_MODE=sandbox` refuses to start unless live actions are off, `LLM_MODE=fake`, every API base URL is `sandbox.invalid`, loopback or the simulator container `sim`, and every key starts with `sandbox-` | `backend/app/core/config.py` |
| No `.env` | In sandbox mode settings come from the process environment only | `get_settings()` |
| Adapters | Without a simulator, `get_adapters()` raises `sandbox_adapters_missing` instead of falling back | `backend/app/platform/__init__.py` |
| Socket guard | The API, the worker and the CLI patch `socket` at start: only loopback, the database host and (drill) the simulator can be reached | `backend/app/core/netguard.py` |
| Docker network | Application containers are only on an `internal` network with no route out. Ports are published by forwarder containers that hold no application code | `compose.sandbox.yaml` |
| Observation | `APP_MODE=observe` needs live actions off, a fake model and a database whose name contains `observe`. Its transport refuses every method except GET/HEAD and every gateway path before a request is built; it keeps its own mailbox cursor (`mailbox:observe`); its actions are stored as `observed`, which no mode sends | `platform/readonly.py`, `platform/mailbox.py`, `workflows/rfq.py` |
| One worker | A worker that polls takes a database advisory lock; a second one exits | `backend/app/workers/main.py` |
| Supervised trial | `HOLD_NEW_REQUESTS=true` creates every new request paused, with no pricing, portal call or mail until an operator presses Resume | `workflows/rfq.py` |

`make drill-check` proves the network part with deliberate attempts from inside five containers
(TCP to 1.1.1.1:443, HTTPS to example.com, the socket guard, sandbox settings with a live URL):
all 20 are refused.

## What the simulator provides

- **Mailbox**: ids, threads, `references`, attachments with real bytes (PDF), paging, duplicate
  and delayed delivery, captured outbound mail, lost acknowledgements, 429 with `Retry-After`.
- **Customers**: accept only if the quote arrives before the six-minute deadline, matches the
  request, is in the right currency, is within budget, and (for dangerous goods) the documents
  were asked for first. Otherwise they reject with a stated reason.
- **Carriers**: truckers answer pickup enquiries; airlines and the rate desk answer rate
  enquiries (also wrongly, late or not at all) and confirm mailed bookings. Any listed carrier
  is a valid recipient; an unlisted address counts as stray mail.
- **Rate portal and bookings**: several carriers per lane, USD rates with and without FX,
  short-lived rates, 429, 503, timeout and malformed answers, bookings keyed by `team_reference`.
- **Model gateway**: fixture answers, invalid JSON and timeouts, usage labelled synthetic.
- **Runner**: seeded randomness and an injected clock. The same scenario and seed give the same
  `digest`. All 37 scenarios also pass on seeds 7, 99 and 2026.
- **Server** (`backend/app/sandbox/server.py`): the same simulator on real time for the drill,
  with a control port to load a scenario and to hold one mail send before or after acceptance.

API paths and field names follow `docs/contracts.md` and the public API description read on
2026-11-21. Reply formats of airlines, truckers and the rate desk are **assumptions**.

## Evidence

Statuses: PASS (local), INCOMPLETE, BLOCKED (needs an organiser answer), NOT DONE.
**Live integration is unverified for every row.**

Last full run on this branch: 381 tests passed, 1 skipped (the opt-in live portal read, never
run); 37 of 37 scenarios pass with identical digests on two runs; `make drill-check` passes.

| Task | Criterion | Local evidence | Result | What is still missing |
| --- | --- | --- | --- | --- |
| T1 | Restart keeps cursors, work, evidence | cursor continues across runs; drill restarts | PASS | |
| T1 | Paging and duplicates lose and duplicate nothing | `paging_250`, `duplicates` | PASS | client pages at 100, API allows 200 |
| T1 | Two workers cannot make two first quotes | `two_workers`; second drill worker exits | PASS | |
| T1 | A send is delivered once, whoever sends | `test_two_senders_of_one_action_deliver_it_once`; browser race | PASS | |
| T1 | Lost acknowledgement or crash stays UNKNOWN, no resend | `lost_ack`, `crash_recovery`; drill `kill_before_send`, `kill_after_acceptance` (SIGKILL of the worker container) | PASS | after a timeout the API offers no way to ask whether a mail was stored, so a person decides |
| T1 | Paused or expired work starts nothing | `pause_takeover`, `expired_backlog`; browser pause of 100 s | PASS | |
| T1 | A pause may outlast a job's retries | `test_a_job_waits_for_a_paused_session_however_long_the_pause_lasts` | PASS | found and fixed in this change |
| T1 | Mail 429 honours `Retry-After` | `send_rate_limited` | PASS | |
| T1 | Routing by sender, thread and evidence | `tests/router/` | PASS | carrier mail without our reference is recorded but not put on the review list |
| T1 | Gateway usage with cost kind | `test_usage_reports_cost_as_unknown_until_it_is_known` | INCOMPLETE | cost stays `unknown`: no price list is verified |
| T1 | Read-only observation | four observation tests | PASS | never run against the real mailbox; prices in observation come from fixtures, not the live portal |
| T2 | Every tariff file approved or a visible exception | `tests/pricing/test_ingest.py` | PASS | |
| T2 | Independent price fixtures | `test_pricing_oracle.py`, including Elbflug below | PASS | |
| T2 | Missing charge, currency, validity or FX blocks completeness | `usd_lane`, `pickup_no_trucker` | PASS | |
| T2 | Portal faults do not stall other sources | `portal_faults`, `slow_carrier` | PASS | |
| T2 | Portal quota is not exceeded | `test_parallel_requests_never_exceed_the_reported_portal_budget` | PASS | inside one worker only; identical concurrent requests are not combined |
| T2 | A revised tariff is not quoted from the old file, and approval reprices | `tariff_update`, `tariff_revision`, `test_approving_a_revised_tariff_reprices_open_requests_from_the_approved_file` | INCOMPLETE | reading a new file into the approved snapshot is a supervised step with the parser CLI; nothing ingests it automatically |
| T3 | Extract and validate RFQ fields | `happy_path`, `model_extraction`, six unseen wordings | PASS | rules cover IATA codes and kg |
| T3 | Clarify missing information | `missing_info`, `pickup_zone_missing` | PASS | |
| T3 | Dangerous-goods documents validated by content | `tests/sandbox/test_documents.py`, `dg_wrong_documents` | INCOMPLETE | scans and image-only PDFs cannot be read (no OCR in the images): they are refused and the customer is asked again |
| T3 | Which documents are required | `app/workflows/data/dg_requirements.json` | BLOCKED | shipped rule (declaration and safety data sheet) is marked `confirmed: false`: no organiser source |
| T3 | Cheapest and fastest | `fastest`, oracle tests | PASS | |
| T3 | Door pickup priced from a trucker's bid | `pickup_with_trucker`, `pickup_no_reply` | PASS | the first usable bid is used |
| T3 | Airline rates by mail | `email_rate`, `email_rate_unusable_replies`, `tests/sandbox/test_email_sourcing.py` | PASS | reply format assumed; asked only when no portal or tariff price exists |
| T3 | Rate desk | `email_rate_rate_desk` | BLOCKED | what the rate desk answers is undocumented; a reply that names no listed airline needs a person and a manual booking |
| T3 | One first quote; late bids and duplicates change nothing | `duplicates`, `late_bid` | PASS | |
| T3 | Acceptance must match the quote as sent | amount, route, condition and service-level tests | PASS | |
| T3 | Booking by portal, retried with the same identity | `booking_retry`, `rate_expired_at_booking` | PASS | after a failed booking no new offer is sent automatically |
| T3 | Booking by mail | `email_booking`, `email_booking_silent`, `email_rate` | PASS | wording and confirmation format assumed |
| T4 | Journey can be reconstructed | timeline in every session | PASS | |
| T4 | Tracking never invents a status | `happy_path`, tracking test | PASS | |
| T4 | Review queue: visible and actionable | browser run; `test_each_unusable_reply_is_recorded_with_its_reason_and_nothing_is_quoted` | PASS | |
| T4 | Takeover, draft edit, send, UNKNOWN resolution | browser run below | PASS | |
| T4 | Operator commands need a token | browser run (401 without and with a wrong token) | INCOMPLETE | one shared token, no users or roles; off unless `OPERATOR_TOKEN` is set |
| T4 | Event stream | `test_event_stream_resumes_without_repeating` | PASS | the board polls every 2 s, which is enough here |
| T5 | Replay with controlled time, faults, report | `make simulate` | PASS | |
| T5 | Real processes, kills, isolation | `make drill-check` | PASS | |
| T5 | Deployment and live smoke test | | NOT DONE | not authorised; see the trial plan |

### Elbflug FRA-DFW, calculated from the source tariff

Source: `starter-kit/elbflug-air-freight-tariff-2026-11.xlsx`, sheet `Export`.

- Row A22: `DFW | 3 | 108,00 | 2,79 | 2,01 | 1,57 | 1,19 | 0,92 | 0,84`: transit 3 days,
  minimum 108.00, then EUR per kg from 0, 45, 100, 300, 500 and 1000 kg.
- Row A53: `FRA-DFW: fuel 0,74 per kg, security 0,14 per kg`.

Request: 300 kg, 1.2 cbm. Volume weight 1.2 x 167 = 200.4 kg, so the chargeable weight is 300 kg.

| Line | Working | EUR |
| --- | --- | --- |
| Freight, break "from 300 kg" | 300 x 1.19 | 357.00 |
| Fuel | 300 x 0.74 | 222.00 |
| Security | 300 x 0.14 | 42.00 |
| **Total** | | **621.00** |

Freight is above the minimum (108.00), and billing at the next break is dearer (500 x 0.92 =
460.00). `test_elbflug_fra_dfw_matches_a_calculation_from_its_own_tariff_sheet` reads the two
rows from the spreadsheet itself and asserts 621.00 without calling the pricing engine for the
expected value.

### Ledger of all 42 simulated requests (seed 42)

34 quoted, 33 accepted, 31 booked. The full table is in `reports/sandbox-report.md`.

| Outcome | Scenario / request | Why |
| --- | --- | --- |
| Not quoted (4) | `expired_backlog` old1, old2, old3, nearly | delivered after a restart, already past or too close to the six-minute deadline; no late quote is sent |
| Not quoted | `slow_carrier` slow | the only portal airline timed out twice; three airlines and the rate desk were asked by mail, nobody answered; the customer was told a minute before the deadline |
| Not quoted | `pickup_no_reply` rfq1 | three truckers asked, none answered |
| Not quoted | `pickup_no_trucker` rfq1 | no listed trucker serves zone 99; an incomplete price is not quoted |
| Not quoted | `email_rate_unusable_replies` rfq1 | four replies: another route, charges on top, validity passed, no bookable airline named |
| Quoted, not accepted | `customer_rejects` rfq1 | the customer's budget is below the cheapest price (EUR 585.00) |
| Accepted, not booked | `rate_expired_at_booking` rfq1 | the portal rate expired before the acceptance arrived; the customer was told nothing is booked |
| Accepted, not booked | `email_booking_silent` rfq1 | the airline never confirmed the mailed booking; it stays `submitted`, never "confirmed" |

### Usage by endpoint

Simulated counts, not a bill (full table in the report). Per priced request: 2.9 portal rate
reads, 3.2 mail sends. In the drill, one long-running worker handled one request with 1 lanes
read, 3 weight-break reads, 3 rate reads, 1 booking request and 2 mail sends; the lanes and
breaks are then cached for the life of the worker. Mailbox reads depend only on the poll
interval (1.5 s by default: 40 a minute), not on the number of requests. Repeated calls are
listed with their cause in the report. No real cost is known: nothing was sent to the 5U API.

### Operator UI in a real browser (drill stack, real worker, real time)

Buttons were pressed in the page; the outcome was read from the simulator, not from the UI.

| Check | What was done | Result |
| --- | --- | --- |
| Review queue | open `/review` | blocked sessions and two carrier bids listed with their reasons |
| No token | press Pause without a token | 401, session not paused |
| Wrong token | enter a wrong token, press Pause | "Not done: 401 Unauthorized", session not paused |
| Pause holds the worker | pause, customer's answer arrives | 80 s later: no quote at the simulator |
| Resume releases it | press Resume | quote at the simulator within 12 s, then accepted |
| UNKNOWN after a real kill | worker killed mid-send, restarted | session on the review list with `send_unknown` |
| Takeover and edit | pause, "it was not delivered", edit the draft, save, "Approve and send" | the simulator holds exactly one quote, with the edited text |
| Nothing while paused | acceptance arrives during the pause | 0 booking requests |
| Worker continues | press Resume | booking confirmed 2 s later |
| Operator against worker | two sends and a resume at the same instant | one delivered, one refused with `send_in_progress`; one quote at the simulator |
| Review actions | "Mark as handled", "Approve and price with it" | item leaves the list; the approved bid is quoted |

Found and fixed during this run: a refused Pause showed no message; the outbound-actions panel
was squeezed by a shared style; a resolved UNKNOWN stayed on the review list; a pause longer
than about a minute dropped the waiting step.

## Questions for the organisers

1. Dangerous goods: which documents must a request carry before we may quote (declaration,
   safety data sheet, both, or depending on UN number or class)? Are scans accepted?
2. Rate desk: what may we ask `ratedesk@hackloop.mail`, what does a reply contain, does it name
   an airline, and how is such a rate booked?
3. Airlines that are not on the portal: what does a reply to a rate enquiry look like, how fast
   does it come, and how do they confirm a booking sent by mail?
4. Sourcing: must a minimum number of carriers be asked before a quote, or may the first
   complete price be quoted?
5. Scoring: what counts (accepted quotes, price, speed, bookings), and what do a wrong quote, a
   second quote, a clarification question and a "no offer" cost? Does the six-minute clock
   keep running while we wait for the customer's answer?
6. Limits: how many mailbox reads per minute are allowed, and is the webhook preferred? What
   are the portal limits per endpoint, and do refused (429) requests count?
7. Mailbox: which fields describe an attachment on a polled message, and is the deadline
   counted from `received_at`?
8. After a timeout on a mail send, is there any way to check whether the mail was stored?
9. Will airlines send new tariff files during the run, and from when must they be used?
10. Is there a test key, a test mailbox or a non-scored window for a first smoke test, and
    does reading the mailbox alone affect the score?
11. Model gateway: is there a price list and a budget, and can usage be read back?

## Controlled live trial (a plan; nothing here has been run)

Live execution is off: `APP_MODE=demo`/`sandbox`, `LIVE_ACTIONS_ENABLED=false`. Each stage
needs your explicit go-ahead, and stage 2 needs answers to questions 1, 5, 6 and 10 or a
decision to proceed without them.

**Before any stage**

- `make verify` and `make drill-check` pass on the exact commit that will run.
- A new, empty database used for nothing else. One worker. `OPERATOR_TOKEN` set.
- One person watches the review queue for the whole stage.

**Stage 1: observe (read only)**

- Settings: `APP_MODE=observe`, `LIVE_ACTIONS_ENABLED=false`, `LLM_MODE=fake`, database name
  containing `observe`, `MAILBOX_POLL_SECONDS=5`.
- Allowed: `GET /v1/mailbox/messages` and attachment downloads. Nothing else can leave.
- Limit: 30 minutes or 200 mails, whichever comes first (at most 360 reads).
- Check by hand for at least ten requests: kind, extracted fields, dangerous-goods decision,
  and the attachment fields the API really sends. Prices in this stage come from fixtures and
  prove nothing.
- Stop at once if the log shows a refused write: something tried to send.

**Stage 2: supervised live, one request at a time**

- Settings: `APP_MODE=live`, `LIVE_ACTIONS_ENABLED=true`, `HOLD_NEW_REQUESTS=true`,
  `LLM_MODE=fake`, a second new database.
- Every new request arrives paused. The operator releases one, watches it to its end, then
  the next. At most three requests released; at most three bookings.
- Allowed per released request: portal reads, one first quote, clarification mails, one
  booking after an acceptance as quoted, enquiries to at most three carriers and the rate desk.
- Watch: the session timeline, `/review`, `GET /api/usage`, and the worker log for 429s.

**Stop conditions (stop the worker, do not restart it without a decision)**

- Any action `UNKNOWN`, any booking `UNKNOWN`, any second priced mail in one thread.
- A quote whose total a person cannot reproduce from the portal answer or the tariff sheet.
- More than three 429 answers in ten minutes on any endpoint.
- A customer answer the agent classified wrongly, or mail sent to an unexpected address.
- Anything in the log about a refused or blocked request that was not expected.

**Recovery**

- Stop: `docker compose stop worker`. Pending work stays in the database; nothing is lost.
- `UNKNOWN` mail: look in the mailbox, then record "delivered" or "not delivered" on the
  session page. It is never resent on its own.
- `UNKNOWN` booking: the worker asks again with the same `rate_id` and `team_reference`; the
  API returns the existing booking. If it stays unclear, contact the organisers before retrying.
- A wrong quote that went out: pause the session, write to the customer by hand, tell the
  organisers. A second quote in the thread does not count, so it cannot be corrected by sending
  another.
- Restart only after the cause is understood, with `HOLD_NEW_REQUESTS=true` still set.

**Not covered by this plan**: unattended operation, more than one worker, a real model
(`LLM_MODE=gateway`), and deployment. Each needs its own check first.

## Not verified

- Any call to the real 5U API: mailbox, bookings, rate portal, gateway. `LiveMailbox.send`
  and `PortalBooking` are written from the published DTOs and have only met the simulator.
- Observation mode against the real mailbox.
- Reply formats of airlines, truckers and the rate desk; the shape of attachment metadata.
- Writing quality of a real model. The sandbox uses templates.
- The organiser's acceptance rules, sourcing rule and scoring.
