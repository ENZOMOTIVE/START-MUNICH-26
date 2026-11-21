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
