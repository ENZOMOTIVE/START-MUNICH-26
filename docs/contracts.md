# Contracts

Internal models live in `backend/app/contracts/models.py`. Live DTO shapes live in `backend/app/contracts/external.py` and were taken from [https://api.start-hack.5u.ai/openapi.json](https://api.start-hack.5u.ai/openapi.json) (2026-11-21). Adapter protocols: `backend/app/contracts/adapters.py`.

Do not invent extra live endpoints. Re-check [https://api.start-hack.5u.ai/docs](https://api.start-hack.5u.ai/docs) before any live call.

## Money and quantities

| Alias | Meaning | Wire form |
| ----- | ------- | --------- |
| `MoneyStr` | `Decimal` with 2 places | string, e.g. `"412.50"` |
| `QtyStr` | `Decimal` | string, e.g. `"250"` / `"1.20"` |

JSON fixtures and API responses use strings for money and weights. Currency is a separate field (`EUR`).

## Enums

- `DeliveryStatus`: `pending`, `confirmed`, `failed`, `UNKNOWN`
- `WorkflowStatus`: `pending`, `quoted`, `accepted`, `booked`, `expired`, `failed`
- `SessionStage`: `intake`, `pricing`, `quoted`, `accepted`, `booked`, `exception`
- `ServiceIntent`: `cheapest`, `fastest`

## Internal Pydantic models

| Model | Role |
| ----- | ---- |
| `ChargeLine` | `code`, `amount` (`MoneyStr`), `currency`, `unit` (default `shipment`) |
| `Message` | Mail row: `id`, `external_id`, `thread_id`, `sender`, `recipient`, `subject`, `body`, `received_at`, `classification`, `source`, `simulated`, `raw` |
| `RFQ` | Lane + cargo: `origin`, `destination`, `weight_kg`, `volume_m3`, pickup, `service_intent`, `status` |
| `RateCandidate` | Priced option: `carrier`, `carrier_code`, `total`, `transit_days`, `rate_id`, `complete`, `valid_until`, `source_ref`, `source_version`, `charges`, `simulated` |
| `QuoteDecision` | Selected candidate + `evidence` |
| `ActionIntent` | Outbound intent: `action_key`, `kind` in `first_quote` \| `booking` \| `tracking` \| `reply` |
| `Booking` | `team_reference`, `rate_id`, `booking_reference`, `status` |
| `Session` | CRM session keyed by `rfq_id` |
| `SessionEvent` | Timeline event |

## Live DTO shapes (documented)

- `HackathonMessageIn`: `to`, `subject`, `body`, `in_reply_to`, `attachments`
- `HackathonBookingIn`: `rate_id`, `team_reference`
- `HackathonBookingOut`: `booking_reference`, `team_reference`, `rate_id`, `airline`, `airline_code`, `price`, `currency`, `status`
- `HackathonRate`: `rate_id`, `airline`, `airline_code`, origin/destination, weights, `total`, `currency`, `fx_eur_per_usd`, `transit_days`, `valid_until`, `dataset_version`

Poll payloads are `additionalProperties` in the spec. Persist raw JSON; only read documented fields.

## Adapter protocols

- `MailboxAdapter.poll(since_id, limit)` / `.send(payload)`
- `PortalAdapter.get_rate(rfq, airline)` / `.book(rate_id, team_reference)`
- `CarrierAdapter.enquire(rfq, carrier_email)` — email placeholder; live send uses mailbox
- `GatewayAdapter.complete(prompt, model)`
- `BookingAdapter.confirm(candidate, team_reference)` — demo always simulated

## Official live API

Base: `https://api.start-hack.5u.ai`. Docs: [https://api.start-hack.5u.ai/docs](https://api.start-hack.5u.ai/docs). Only these documented paths:

| Method | Path |
| ------ | ---- |
| GET | `/v1/mailbox/messages` (`since_id`, `limit` max 200) |
| POST | `/v1/mailbox/messages` |
| GET | `/v1/mailbox/messages/{message_id}` |
| GET | `/v1/mailbox/messages/{message_id}/attachments/{attachment_id}` |
| GET | `/v1/portal/ports` |
| GET | `/v1/portal/brackets` |
| GET | `/v1/portal/rates` |
| POST | `/v1/portal/bookings` |
| GET/POST | `/v1/gateway/openai-key` |
| GET | `/v1/gateway/models` |
| POST | `/v1/gateway/chat/completions` |
| POST | `/v1/gateway/responses` |
| POST | `/v1/gateway/embeddings` |
| GET | `/v1/me` |
| GET | `/v1/board` |
| GET | `/v1/board/me` |

Local app routes (`GET /health/live`, `POST /internal/demo/run`) are ours, not the competition API.

## OpenAPI generation (our app)

```sh
make openapi
```

Runs `uv run python scripts/export_openapi.py > ../frontend/openapi.json` from `backend/`, then `pnpm openapi` in `frontend/`. Paths are quoted in the Makefile because the repo path contains spaces.

## Mail routing (T1)

Every inbound mail gets one kind in `messages.classification` and one owning agent. Code in `backend/app/platform/routing.py` decides, in this order:

1. A kind already set at ingest (fixtures and the demo set `rfq` / `acceptance`) is kept.
2. Subject matches `Booking BK-XXXXXXXX confirmed` (any sender): `booking_confirmation`.
3. Sender `tracking@hackloop.mail`: `tracking`. Sender `ratedesk@hackloop.mail`: `rate_desk_reply`.
4. Sender on the start-kit carrier list: `tariff` for an airline mail with an attachment, otherwise `carrier_bid` (truckers always `carrier_bid`).
5. Any other sender is a customer. A mail that starts a thread is `rfq`. A reply in a thread whose session is not at stage `quoted` is `follow_up`.
6. A customer reply in a `quoted` thread is judged by a multilingual keyword check. A keyword acceptance is final. With `ROUTER_BACKEND=gateway`, every other reply is sent once to `ROUTER_MODEL` through the 5U gateway (labels A/Q/D/U, accepted at probability 0.7 or more); on any gateway error the keyword answer stands. Result: `acceptance`, `follow_up` (question or decline) or `unknown` (unclear).

| Kind | Agent (`raw.routing.agent`) | Handler today |
| ---- | ----- | ------- |
| `rfq` | `rfq_booking` | `app.workflows.rfq:handle_rfq` |
| `acceptance` | `rfq_booking` | `app.workflows.booking:handle_acceptance` |
| `follow_up`, `carrier_bid`, `rate_desk_reply`, `booking_confirmation` | `rfq_booking` | none |
| `tracking` | `track_trace` | none |
| `tariff` | `tariff_ingestion` | none |
| `unknown` | none | none |

### Adding a handler

Add one line to `HANDLERS` in `backend/app/platform/router.py`, e.g. `"tracking": "app.crm.tracking:handle_tracking_mail"`. A handler is `handler(db, message)`: it runs inside the routing job's transaction, must be safe to run twice for the same mail (a failed job is retried), and signals failure by raising. A rerun reuses the stored decision; it does not decide again.

### What the router writes on `messages.raw`

| Key | Written by | Content |
| --- | ---------- | ------- |
| `raw.mail` | mailbox poll | The untouched poll item from `GET /v1/mailbox/messages` (includes `references`, `attachments`). Absent for fixture/demo mail. |
| `raw.routing` | router | `kind`, `agent`, `decided_by` (`preset` \| `sender_rule` \| `thread_rule` \| `keyword` \| `gateway` \| `fallback`), `rule`, `sender` (`email`, `name`, `kind`: `airline` \| `trucker` \| `rate_desk` \| `tracking` \| `customer`), `starts_thread`, `model`, `probabilities` (gateway label probabilities, e.g. `{"A": 0.93}`), `error` |
| `raw.rfq` | RFQ agent (T3) | Fields `handle_rfq` reads. The router does not write it. |

### Timeline event

When no handler runs for a mail and its thread has a session, the router appends a `mail_routed` session event with payload `external_id`, `kind`, `agent`, `decided_by`, `sender`, `simulated`. Mail whose thread has no session (bids, tariffs, tracking questions, unknown new mail) gets no event.

### Replying and threads

- Reply target: `in_reply_to = int(message.external_id)`. `external_id` is the 5U mailbox id as a string.
- `thread_id` is the first id in the mail's `references`, or the mail's own id when it starts a thread. A carrier's bid arrives in the thread of the enquiry we sent, not the customer's thread, so keep the id of each enquiry.

### Live poll item (confirmed 2026-11-21)

`GET /v1/mailbox/messages` returns `{"messages": [...], "next_since_id": int}`. Each message has `id` (int), `from`, `to`, `subject`, `body` (strings), `in_reply_to` (int or null), `references` (list of ids, empty for a thread starter), `received_at` (ISO-8601 with offset) and `attachments` (list). No rate-limit headers were returned on the poll. Customers write from `@hackloop.mail` addresses that are not on the carrier list.

### Router settings

| Variable | Default | Meaning |
| -------- | ------- | ------- |
| `ROUTER_BACKEND` | `keyword` | `keyword` (offline) or `gateway` (replies the keyword check cannot accept go to the gateway model; counts as gateway spend) |
| `ROUTER_MODEL` | empty | Gateway model for that step; must be a non-reasoning model that supports `logprobs`. Empty means the keyword answer is kept |
| `MAILBOX_POLL_SECONDS` | `1.5` | Minimum gap between mailbox polls (live only) |
