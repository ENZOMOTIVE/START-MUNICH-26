# Tasks

## 1. Rule-based routing (offline)

- [x] 1.1 Merge `origin/main` into `T1-router-impl` (two commits: T2 pricing and the demo fixture fix); verify `uv run pytest -q` passes on the merged branch before any other change
- [ ] 1.2 Add `router_backend` (`keyword` default, `gateway`), `router_model` and `mailbox_poll_seconds` (default 1.5) to `core/config.py` and `.env.example`, and tell the team about the shared-file edit; verify `Settings()` loads with an empty environment and the suite still passes
- [x] 1.3 Bundle a copy of `starter-kit/carrier-list.csv` under `backend/app/platform/data/` and load sender name and kind from the repo file when present, else the bundled copy, with a case-insensitive lookup; verify with unit tests that an airline, a trucker and an unknown address resolve correctly and that the loader works with the repo file path pointed at a missing folder
- [x] 1.4 Write the routing decision as a pure function over a mail, its thread position and its thread stage, covering preset kind, sender rules and thread rules; verify with one unit test per scenario under "Route known senders by address" and "Route customer mail by thread position" in `specs/mail-routing/spec.md`, placed in `backend/tests/router/`
- [x] 1.5 Add the keyword backend for the reply question (accept, question, decline, unclear) and keep `classify(message)` importable; verify `test_classify_acceptance_despite_rfq_subject` passes unchanged and new tests cover a clear acceptance, a question and an unclear reply

## 2. Router wiring and dispatch

- [x] 2.1 Rework `route_message` to call the decision function, store the kind in `messages.classification` and the record in `raw["routing"]` (kind, agent, decided_by, rule, sender name and kind); verify with tests that each of the nine kinds can be stored and that a trucker bid records `sender.kind = trucker`
- [x] 2.2 Add the dispatch table (kind to handler, kind to agent) with `rfq` and `acceptance` filled in and the handler contract written as a comment at the table; verify with tests that a `carrier_bid` job ends `done` with no session created and that routing the same rfq mail twice creates one RFQ
- [x] 2.3 Append a `mail_routed` session event when no handler runs and the thread has a session; verify with tests that a `follow_up` reply in a quoted thread adds one event and that a mail with no session adds none
- [x] 2.4 Run the existing suite without editing `test_foundation.py`; verify `test_demo_journey`, `test_duplicate_intake`, `test_demo_mode_no_live_http` and T2's `tests/pricing` pass with default settings, and that `ingest_message`, `FakeMailbox`, `LiveMailbox`, `deliver_intent`, `classify`, `route_message`, `process_jobs_once` and `get_adapters` still import from their current modules
- [x] 2.5 Document the kinds, the agent mapping, the `raw["mail"]` and `raw["routing"]` layout, the `mail_routed` event, the reply target (`int(message.external_id)`) and how to add a handler in `docs/contracts.md`; verify the documented field names match a row produced by the tests

## 3. Live mailbox polling

- [x] 3.1 Implement `LiveMailbox.poll` (GET `/v1/mailbox/messages` with `since_id`, `limit`, team key header, short timeout); verify with stubbed-transport tests for a normal page, an empty page, a 429 and a 500
- [x] 3.2 With the user's permission and the team key, make one live poll and save the response in the session scratch directory; verify by listing the real field names for sender, subject, body, timestamp, references and attachments, noting any rate-limit headers, and recording both in `docs/contracts.md`
- [x] 3.3 Write the poll-item mapping (`external_id`, `thread_id` from `references[0]` or own id, starts-thread flag, original item under `raw["mail"]`, `simulated=false`) using the confirmed names; verify with a test built from the captured response covering a thread starter and a reply
- [x] 3.4 Add the poll step to the worker loop: live only, at most once per `mailbox_poll_seconds`, ingest and advance the cursor in one transaction, log and continue on error; verify with tests that a re-delivered page creates no duplicate message or job and that a failed poll leaves the cursor unchanged
- [x] 3.5 Add attachment download to the mailbox adapter (GET `/v1/mailbox/messages/{id}/attachments/{attachment_id}`, refused when live actions are off); verify with stubbed-transport tests for a successful download and for the refusal in demo mode
- [x] 3.6 With the user's permission, stop the compose worker and run the worker on the host in live mode with the keyword backend against the compose Postgres, then check one real mail end to end; verify by reading the stored row (stored once, classified, `raw["routing"]` present)

## 4. Gateway model as backup for the reply question

Jev work done earlier (request builder, backend, fallback) is removed by 4.1; the probe in the old 4.4 returned 401 for every request because the team has no TypeSafe access.

- [x] 4.1 Remove the Jev backend, `app/platform/jev_questions.py`, `tests/router/test_jev.py` and the `typesafe_api_base`, `typesafe_api_key` and `jev_model` settings from `core/config.py` and `.env.example`; verify `grep -ri "typesafe\|jev" backend .env.example` finds nothing and the suite passes
- [x] 4.2 Implement `LiveGateway.complete` (POST `/v1/gateway/chat/completions` with the team key, 5 s timeout, refused when live actions are off) returning text, token usage and logprobs when present, and storing one `gateway_usage` row per call; verify with stubbed-transport tests for a success (row stored, not simulated), an HTTP error and a timeout (exception, no text) and the refusal in demo mode
- [x] 4.3 Write the gateway reply judge: wording, the four single-letter labels and the 0.7 threshold in one module; one completion at temperature 0, `max_tokens` 1, `logprobs` with `top_logprobs`; label alone when no logprobs are returned; keyword answer on timeout, HTTP error or an unexpected label, with the error recorded; verify with stubbed-transport tests for each label, a below-threshold answer, a missing-logprobs answer, an unexpected label and a timeout
- [x] 4.4 Combine: a keyword acceptance is final with no model call; any other reply to a quote is sent to the gateway judge when `ROUTER_BACKEND=gateway`; `decided_by` records `keyword`, `gateway` or `fallback` and the model, label probabilities and error are stored in `raw["routing"]`; verify with tests that a keyword acceptance makes no call, a keyword question is sent to the model, and the default configuration makes no network request
- [x] 4.5 With the user's permission and the team key, call `GET /v1/gateway/openai-key` and `GET /v1/gateway/models` (read-only); if no OpenAI key is stored, ask the user before storing `GATEWAY_OPENAI_KEY` with `POST /v1/gateway/openai-key`, because that replaces any key a teammate stored; then set the default `ROUTER_MODEL` to a small listed model; verify by recording the stored-key status and the chosen model in design.md
- [ ] 4.6 With the user's permission, send the eight probe replies from the Jev test through the gateway judge; verify by saving each request and answer in the session scratch directory and recording, per reply, the keyword verdict, the model label and probability, the latency and the tokens used
- [x] 4.7 Update `docs/contracts.md` "Mail routing (T1)": step 6 and the `decided_by` values describe the keyword-first gateway backup, and the settings table lists `ROUTER_BACKEND` (`keyword` | `gateway`), `ROUTER_MODEL` and `MAILBOX_POLL_SECONDS` with no Jev settings; verify `grep -i "jev\|typesafe" docs/contracts.md` finds nothing

## 5. Integration checks and handoffs

- [x] 5.1 Run `uv run ruff check .` and `uv run pytest -q` in `backend/` against the compose Postgres; verify both pass
- [x] 5.2 Run `make dev` and trigger `POST /internal/demo/run`; verify the session reaches `booked`, the worker container starts without a `starter-kit/` folder, and the logs show no request to the 5U or TypeSafe hosts
- [ ] 5.3 Tell T3 (issue #23): live RFQ mail arrives without `raw["rfq"]` and needs field extraction; the reply target is `int(message.external_id)`; carrier bids arrive in the thread of the enquiry mail, so its id must be kept; `commit_first_quote` uses a hard-coded `FakeMailbox()`; verify by a posted note
- [ ] 5.4 Tell T2 (issue #22): `tariff` and `carrier_bid` have empty dispatch slots, attachment bytes come from the mailbox adapter, and the parser's optional dependencies are not in the backend image; verify by a posted note
- [ ] 5.5 Tell T4 (issue #24): the new `mail_routed` timeline event, and that mail with no session and RFQs that fail before a session exists have no screen; verify by a posted note
- [ ] 5.6 Ask T5 (issue #25) to let `.env` control `APP_MODE` and `LIVE_ACTIONS_ENABLED` in `compose.yaml`; verify by a posted note, and once changed, that `make dev` with live settings starts the poll
