# 5U START Munich

Shared hackathon codebase for mailbox intake, simulated pricing, quoting, booking, and a CRM dashboard. Default mode is demo: no live email, portal booking, or gateway spend.

## Prerequisites

- Docker
- [uv](https://docs.astral.sh/uv/)
- pnpm
- Node 22
- Python 3.12 (via uv)

## Ports (loopback only)

| Service  | Bind                    |
| -------- | ----------------------- |
| Postgres | `127.0.0.1:5433`        |
| API      | `127.0.0.1:8000`        |
| Frontend | `127.0.0.1:5173`        |

## Commands

```sh
make setup      # copy .env.example if missing; uv sync; pnpm install
make dev        # docker compose up --build -d
make migrate    # alembic upgrade head (compose exec api)
make demo       # POST /internal/demo/run (simulated happy path)
make test       # postgres + pytest
make lint       # ruff
make typecheck  # frontend pnpm typecheck (Python: use make lint)
make quality-data # audit demo fixtures against read-only starter-kit inputs
make logs
make down       # docker compose down  (does not pass -v; data volume kept)
make openapi    # export OpenAPI into frontend/
```

`make down` never uses `-v`. The `fiveu_pgdata` volume stays.

## Demo mode

`APP_MODE=demo` and `LIVE_ACTIONS_ENABLED=false` (see `.env.example`). The demo runner loads fixtures under `fixtures/` and does not send live mailbox mail.

Happy path: inbound RFQ → persist → route → fixture prices → cheapest Yellowtail → quote → acceptance → booking confirmed → CRM timeline → dashboard.

## Docs

- [Architecture](docs/architecture.md)
- [Contracts](docs/contracts.md)
- [Team ownership](docs/team-ownership.md)
- [Runbook](docs/runbook.md)
- Tasks: [T1](docs/tasks/T1.md) [T2](docs/tasks/T2.md) [T3](docs/tasks/T3.md) [T4](docs/tasks/T4.md) [T5](docs/tasks/T5.md)

Official live API (documented endpoints only): [https://api.start-hack.5u.ai/docs](https://api.start-hack.5u.ai/docs)
