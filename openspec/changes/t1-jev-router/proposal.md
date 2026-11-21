# Proposal

## Why

The router is a two-keyword stub that knows only `rfq` and `acceptance`, and nothing polls the competition mailbox, so no live mail reaches any agent. The first scored request arrives at 16:00 on 21 November 2026, and routing is the step every other agent waits on.

## What Changes

- The worker polls the team mailbox with a persisted cursor, stores every mail once, and queues it for routing.
- The router decides which agent handles each mail: the RFQ and booking agent, the track and trace agent, the tariff ingestion agent, or none.
- Routing is decided in code wherever the envelope decides it: sender address, attachments, and whether the mail starts a thread or replies in one.
- One judgment needs language understanding: what a customer's reply to a quote means (accepts, asks something, declines). A multilingual keyword check answers it. When the check cannot confirm an acceptance, an OpenAI model through the 5U gateway can be asked instead.
- Every routing decision is stored with what decided it and who the sender is, and replies that no agent handles show up on the session timeline, so an operator can see them.
- The mailbox adapter can fetch a mail's attachments, so the tariff ingestion agent can be attached to `tariff` mail.
- The model step is off by default. With it off or failing, the keyword answer stands, so demo mode and tests stay offline.
- A minimal live gateway adapter sends one chat completion through the 5U gateway and records the call, for the router and for later use by other agents.
- Jev (TypeSafe) is dropped: the team has no TypeSafe access, it sits outside the 5U gateway, and whether 5U allows it was never confirmed.

Out of this change (still open T1 or T3 work): extracting RFQ fields from mail text and live outbound sending.

## Capabilities

### New Capabilities

- `mailbox-intake`: polling the team mailbox, storing each mail exactly once with its thread, queueing it for routing, and giving agents access to attachments.
- `mail-routing`: deciding the kind of each inbound mail and which agent owns it, recording the evidence, and dispatching.
- `llm-gateway`: calling OpenAI models through the 5U gateway and recording each call, with no spend in demo mode.

### Modified Capabilities

None. The only existing spec work is T2's `pricing` change, which this does not alter.

## Impact

- **Code (T1-owned):** `backend/app/platform/{router,mailbox,gateway,jobs,__init__}.py`, a decisions module and a bundled copy of the carrier list under `backend/app/platform`, `backend/app/workers/main.py`, new tests under `backend/tests/router/`.
- **Shared files (coordinate before editing):** `backend/app/core/config.py` and `.env.example` (three new settings: `ROUTER_BACKEND`, `ROUTER_MODEL`, `MAILBOX_POLL_SECONDS`). `.env.example` changed on main since this branch was cut, so main is merged first. No change to `contracts/`, `db/models.py`, migrations, `compose.yaml`, `fixtures/` or `backend/tests/test_foundation.py`.
- **Names other tasks import stay as they are:** `ingest_message`, `FakeMailbox`, `LiveMailbox`, `deliver_intent`, `classify`, `route_message`, `process_jobs_once`, `get_adapters`.
- **T2 (pricing, merged on main):** no code change needed. `tariff` and `carrier_bid` mail gets a dispatch slot; the routing record tells airline from trucker, which T2's pickup pricing needs. T2's tariff parser takes a folder of files, which the new attachment access can fill.
- **T3 (workflows):** live RFQ mail reaches `handle_rfq` without a `raw["rfq"]` block and ends as `incomplete_rfq` until fields are extracted from the mail text. T3 also needs two facts documented here: the mailbox id to reply to is the stored `external_id`, and a carrier's bid arrives in the thread of the enquiry mail T3 sent, not the customer's thread.
- **T4 (CRM and frontend):** no code change needed. Routed replies appear as a `mail_routed` timeline event, which the timeline already renders. Mail with no session (bids, tariffs, tracking questions, unknown new mail) is stored but has no screen yet.
- **T5 (compose, CI, fixtures):** `compose.yaml` hard-codes demo mode for the api and worker, which overrides `.env`, so live polling cannot be switched on through `make dev` today. The worker image also contains no `starter-kit/`. CI needs no change: defaults stay offline and no dependency is added.
- **External services:** only the 5U API (`https://api.start-hack.5u.ai`): the mailbox, and, when the model step is enabled, the gateway, which forwards customer replies to OpenAI. The gateway needs the team's own OpenAI key stored with 5U, which quote emails need anyway.
- **Scoring:** each model call is gateway spend and counts toward the LLM-cost criterion (full points at no spend, zero at 0.10 USD per accepted quote). With a small model a call costs a small fraction of a cent, and it runs only for replies to quotes that the keyword check cannot settle.
