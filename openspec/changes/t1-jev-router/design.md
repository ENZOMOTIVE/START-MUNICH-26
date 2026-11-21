# Design

## Context

See proposal.md for motivation. What shapes the approach:

- `platform/router.py` has `classify(message)` (keywords) and `route_message`, which dispatches to `handle_rfq` and `handle_acceptance` through lazy imports. A test pins `classify`.
- `platform/mailbox.py` has `ingest_message` (idempotent on `external_id`, enqueues `route_message`). `db/repositories.py` has `get_cursor`, `advance_cursor`, `session_by_thread` and `append_event`. Nothing calls the cursor helpers: the worker only processes jobs.
- The 5U spec leaves mail objects untyped. Prose names `id`, `references`, `attachments` and `next_since_id`. There is no thread id, sender or timestamp field in the spec.
- Sessions carry a `stage` (`intake`, `pricing`, `quoted`, `accepted`, `booked`, `exception`), so "is a quote awaiting an answer in this thread" is a database lookup.
- The 5U gateway is OpenAI Chat Completions at `/v1/gateway/chat/completions`, authenticated with the team key. It uses the team's own OpenAI key, stored once with `POST /v1/gateway/openai-key` (a new key replaces the old one); `GET /v1/gateway/openai-key` says whether one is stored, and `GET /v1/gateway/models` lists the allowed models. Every call counts as gateway spend.
- Jev was tried and dropped: the key tried was a Google AI Studio key, TypeSafe returned 401, and the team has no TypeSafe access.
- `test_demo_mode_no_live_http` fails on any `httpx.Client.request` in demo.
- State of the other tasks, checked on `origin/main` at commit `0941f8a`: T2's pricing package is merged and touches nothing under `platform`, `core`, `contracts`, `db`, `workers` or `workflows`. T3 and T4 have no branch yet. This branch is two commits behind main.

```
worker loop
  +-- poll mailbox (live only) --> ingest_message --> job: route_message
  +-- process jobs --> route_message(mail)

route_message(mail)
  1 preset kind?                          --> keep
  2 sender = tracking address             --> tracking
    sender = rate desk address            --> rate_desk_reply
    sender in carrier list                --> booking_confirmation (subject)
                                              tariff (airline + attachment)
                                              carrier_bid (otherwise)
  3 customer, starts a thread             --> rfq
    customer, reply, thread not "quoted"  --> follow_up
  4 customer, reply, thread "quoted"      --> keyword check
                                              accept --> acceptance (no model call)
                                              else, if ROUTER_BACKEND=gateway:
                                                ask the gateway model (error --> keyword answer)
                                              --> acceptance / follow_up / unknown
  then: store kind + raw["routing"], run the handler if one is registered,
        otherwise add a timeline entry when the thread has a session
```

Steps 1 to 3 are plain code and give the same answer for the same mail every time. Only step 4 involves judgment.

## Goals / Non-Goals

**Goals:**

- A router that works on live mail with no model at all, so the model is an improvement to one branch and never a dependency.
- Each step testable offline as a pure function over a mail and its thread stage.
- Other agents attach by adding one entry to a dispatch table, without editing each other's files.

**Non-Goals:**

- Extracting RFQ fields from mail text, writing emails, live sending, gateway cost accounting beyond one usage row per call.
- Handlers for kinds other than `rfq` and `acceptance`.
- Parsing tariffs or bids. Only access to attachment bytes is provided.
- Any change to the database schema, `compose.yaml`, shared fixtures or the frontend.

## Decisions

### 1. Decide in code first, in a fixed order

Sender address, attachments and thread position are facts on the envelope. Rules over them are exact, free, instant, and cannot be steered by what the mail body says. A model is asked only where language has to be understood.

*Alternative:* a model classifies every mail into nine kinds. Rejected: it spends judgment on facts the envelope states, makes routing vary with wording, and makes a model outage a routing outage.

### 2. Thread position decides new request versus reply

5U scores a quote only as a reply in a thread a customer started, so a customer mail with no `references` is a new request. A customer reply is only interesting when the thread's session is at stage `quoted`; in every other stage it is a `follow_up`.

*Alternative:* ask the model whether a mail is a new request. Unneeded, and a wrong answer there loses a scored request.

### 3. Keyword check first, gateway model only for what it cannot settle

A false acceptance books a shipment nobody ordered, and a missed one loses a booking; `unknown` exists so that an unclear reply goes to an operator instead of silently going either way.

The keyword check reads only the customer's own words (quoted earlier mail is dropped, in English, German, French and Spanish reply formats). Refusals and negated acceptances win first. Acceptance phrases come in two tiers, in English, German, French, Spanish, Portuguese, Italian and Dutch: strong ones ("we accept", "please book", "wir nehmen das Angebot an") accept even when the mail also asks something; weak ones ("sounds good", "d'accord", "va bene") accept only when the mail asks nothing. Talking about accepting ("before we accept", "should we go ahead") never accepts, and a conditional acceptance ("as long as…") is not an acceptance.

A keyword acceptance is final and makes no model call: strong phrases are precise, and this keeps the common case free and deterministic. Every other reply to a quote goes to the gateway model when `ROUTER_BACKEND=gateway`, because those are the replies a phrase list misreads (an unfamiliar language, an acceptance worded loosely). A refusal or question from the keyword check is not trusted alone for the same reason: misreading an acceptance as a question loses the booking.

### 4. How the gateway model is asked

One chat completion, temperature 0, `max_tokens` 1, with `logprobs` and `top_logprobs`. The system message describes four labels as single capital letters (`A` accepts the quote, `Q` asks something about it, `D` declines it, `U` none of these or unclear) and the user message holds the reply's own words and the quoted total. Single letters make the answer one token, so its probability is read straight from `top_logprobs`. The top label decides when its probability is at least 0.7; below that, or with `U`, the kind is `unknown`. If the gateway returns no logprobs, the label alone decides and the record says no probability was available. A timeout (5 s), an HTTP error or a reply that is not one of the four letters falls back to the keyword answer and records the error. The model is `ROUTER_MODEL`, chosen from `GET /v1/gateway/models`.

Wording, labels and the threshold live in one module.

Checked live on 21 November: `GET /v1/gateway/openai-key` returned `set: false`, so no OpenAI key is stored with 5U yet and every gateway call fails until one is stored. `GET /v1/gateway/models` lists 32 models; the `gpt-5` and newer families and `o3` are reasoning models, which do not fit a one-token labelled answer with logprobs. Default `ROUTER_MODEL` is `gpt-4.1-mini` (listed, non-reasoning).

*Alternative:* structured JSON output with an enum. Equally constrained, but its probability is spread over several tokens and is harder to read.

### 5. Comparison with Jev

| | Jev (as planned) | Keyword first, gateway model backup |
| --- | --- | --- |
| Available to the team | No (no TypeSafe access) | Yes, through the 5U gateway |
| Allowed by 5U | Unconfirmed | Yes: OpenAI through the gateway is the documented path |
| Common case (clear acceptance) | One network call, 70 to 500 ms (vendor figure) | No call, under 1 ms, deterministic |
| Hard case (loose wording, other languages) | One call | One call; latency typically about a second (not measured yet) |
| Probability to threshold | Calibrated, per the vendor | Token logprob, if the gateway returns it |
| Cost | Outside the gateway, not counted | Counted as gateway spend; a small fraction of a cent per call with a small model, only on hard cases |
| Accuracy on this task | Not measured: every probe returned 401 | Keyword: 8 of 8 probe replies and 24 further cases in the tests. Model: measured in task 4.6 |

Latency matters little here: a reply to a quote is not on the scored response-time path, which runs from the RFQ to the first quote. The honest gap is calibration: a token logprob is a weaker signal than Jev's calibrated probability, which is why the keyword check, not the model, makes the one decision that books a shipment.

### 6. Dispatch table in the router, with a stated handler contract

A mapping from kind to a handler reference (module path and function, imported lazily as today) and a mapping from kind to owning agent. A handler is `handler(db, message)`, runs inside the routing job's transaction, must be safe to run twice for the same mail, and signals failure by raising. `rfq` and `acceptance` are filled in. Expected owners of the empty slots, from the task briefs and T2's merged code:

| Kind | Agent | Likely home |
| --- | --- | --- |
| `rfq`, `acceptance`, `follow_up`, `rate_desk_reply`, `booking_confirmation` | RFQ and booking | `app/workflows` (T3) |
| `carrier_bid` | RFQ and booking | `app/workflows` (T3 parses the mail; T2's `candidate_from_bid` prices it) |
| `tariff` | Tariff ingestion | `app/pricing/parsers` (T2) |
| `tracking` | Track and trace | `app/crm/tracking.py` (T4) |

*Alternative:* a `register()` call made by each module at import time. More decoupled, but import order then decides whether a handler exists, which is hard to debug overnight.

### 7. Routing record in `raw["routing"]`, original payload in `raw["mail"]`

`raw["routing"] = {kind, agent, decided_by, rule, sender: {name, kind}, model, probabilities, error}` and `raw["mail"]` holds the untouched poll item, including `references` and `attachments`. `sender.kind` is `airline`, `trucker`, `rate_desk`, `tracking` or `customer`; T2 prices pickup from trucker bids, so the distinction is worth recording once. This needs no migration and no edit to shared model files. `raw["rfq"]` stays reserved for the RFQ agent.

*Alternative:* new columns. Cleaner, but a migration and two shared files for no change in behaviour.

### 8. Carrier list bundled with the package

The backend image copies only `app`, `migrations` and config, so `starter-kit/` does not exist inside the worker container. A copy of `carrier-list.csv` ships under `backend/app/platform/data/`, and the loader prefers the repo's `starter-kit/` file when it is present. T2 solved the same problem the same way for `pickup_zones.csv`.

*Alternative:* mount `starter-kit/` in `compose.yaml`. That is a T5-owned shared file and leaves the image dependent on a mount.

### 9. Unhandled replies become a timeline event

When no handler runs and `session_by_thread` finds a session, the router appends a `mail_routed` event with the kind, the mail's `external_id` and what decided it. The frontend timeline prints any event type as it comes, so this needs no T4 change. Handled kinds are left alone because their handlers already write their own events.

*Alternative:* a messages API and screen. The right long-term answer for mail with no session, but it is T4's surface.

### 10. Polling in the worker loop

The worker polls when live actions are allowed and at least `MAILBOX_POLL_SECONDS` (default 1.5) have passed, ingests each item and advances the cursor in the same transaction, then processes jobs. Mapping: `external_id = str(id)`, `thread_id = str(references[0])` or `str(id)`, `received_at` = now when the payload has no timestamp. Sender, subject and body field names are confirmed from the first live response. The mailbox adapter also gains one read: an attachment's bytes by mail id and attachment id.

*Alternative:* a separate poller service. A second process to keep alive overnight and a compose change.

### 11. Live runs happen outside compose for now

`compose.yaml` sets `APP_MODE: demo` and `LIVE_ACTIONS_ENABLED: "false"` under `environment:` for the api and worker, and that takes precedence over `.env`. Until T5 changes it, the live worker is started on the host against the compose Postgres (`127.0.0.1:5433`) with the live variables set in that shell. Stop the compose worker first so two workers do not share one cursor carelessly; job claiming uses `SKIP LOCKED`, and message intake is idempotent, so an overlap is safe but wasteful.

*Alternative:* edit `compose.yaml` here to pass the variables through. It is T5's file; asked for as a coordination item instead.

### 12. Build order: rules, then live mail, then the model backup

Rules and dispatch are built and tested first against fixtures, then live polling, then the gateway model backup. After the second step the system routes real mail with the keyword check alone, so the model step can be added, tuned or switched off without blocking anyone.

## Integration with the other tasks

| Task | What this change relies on | What they need to know or do |
| --- | --- | --- |
| T2 pricing (merged) | Nothing at runtime. | A `tariff` handler can call the mailbox adapter for attachment bytes and feed T2's folder-based ingest. Bid mail carries `sender.kind`. The parser's optional dependencies and OCR tools are not in the backend image. |
| T3 workflows | `handle_rfq(db, message)` and `handle_acceptance(db, message)` keep their signatures and stay idempotent. `session.stage == "quoted"` means a quote awaits an answer. | Extract RFQ fields from mail text into `raw["rfq"]` or inside the workflow. Reply with `in_reply_to = int(message.external_id)`. Keep the id of each enquiry sent to a carrier, because the bid's thread is that enquiry, not the customer's thread. `commit_first_quote` still uses a hard-coded `FakeMailbox()`. |
| T4 CRM and frontend | The timeline renders unknown event types as text. | A new `mail_routed` event appears. Mail with no session has no screen. An RFQ that fails before a session exists (`incomplete_rfq`) is also invisible. |
| T5 compose, CI, fixtures | CI runs in demo with no keys. | Let `.env` control `APP_MODE` and `LIVE_ACTIONS_ENABLED` in `compose.yaml`. New tests live in `backend/tests/router/` with their own sample mails, so `fixtures/` and `test_foundation.py` are untouched. |

## Risks / Trade-offs

- [Live RFQ mail has no `raw["rfq"]`, so `handle_rfq` ends in `incomplete_rfq` and, since no session exists yet, nothing shows in the dashboard] → routing is correct but no quote goes out until fields are extracted. Raised with T3 as the first follow-up.
- [Compose forces demo mode] → live worker runs on the host until T5 changes the file.
- [Each model call is gateway spend and lowers the LLM-cost score] → the model runs only on replies to quotes that the keyword check cannot accept, with one output token and a small model.
- [Storing the OpenAI key with 5U replaces any key a teammate stored] → check `GET /v1/gateway/openai-key` first and store only with the user's confirmation.
- [The gateway may not return logprobs] → the label alone decides and the record says so; checked on the first live call.
- [Text of customer replies goes to OpenAI through the gateway] → the mail is produced by 5U's simulation, not real customers. Do not enable this on real mail without a data-protection review.
- [A reply written to steer the model] → the model can only pick one of four labels, it is never asked about a reply the keyword check accepted, and acceptance also requires the thread to hold a quote.
- [The bundled carrier list goes stale if 5U changes carriers during the hourly setup changes] → the repo's `starter-kit/` file wins when present, and an unknown sender falls to the customer rules, where a new-thread mail becomes an `rfq` that fails as incomplete with nothing sent.
- [A tariff pasted into a mail body, with no attachment, is classified `carrier_bid`] → both kinds are stored only, for now; a subject rule is added once a real tariff mail is seen.
- [The mailbox poll shares the team key with T2's portal reads, whose limit is 10 a minute] → the 5U spec states rate limits only for portal rates and for sending, not for polling; the first live poll task checks the response headers.
- [Live mail field names are unverified] → the payload is kept verbatim and the mapping is one function with a test built from a captured response.
- [A slow poll delays job processing] → short poll timeout, at most one poll per interval.

## Migration Plan

No schema change. Merge `origin/main` into the branch first. Default settings behave as today. To poll, run the worker on the host with `APP_MODE=live` and `LIVE_ACTIONS_ENABLED=true`; add `ROUTER_BACKEND=gateway` and `ROUTER_MODEL` to enable the model backup. Rollback is stopping that worker and restarting the compose one.

## Open Questions

- Exact names of the sender, subject, body and timestamp fields in a live poll item. Answered by the first live poll; affects only the mapping function.
- Whether the gateway returns logprobs, and which models it lists. Answered by the first live gateway call; affects only the default `ROUTER_MODEL` and whether a probability is recorded.
