# Runbook

## Prerequisites

- Docker
- uv
- pnpm
- Node 22

Copy env once: `make setup` copies `.env.example` to `.env` only if `.env` is missing.

## Ports

All binds are loopback.

- Postgres: `127.0.0.1:5433` (user/password/db `fiveu`; host 5432 is often taken)
- API: `127.0.0.1:8000`
- Frontend: `127.0.0.1:5173`

Host `DATABASE_URL`: `postgresql+psycopg://fiveu:fiveu@127.0.0.1:5433/fiveu`  
Compose `DATABASE_URL` for api/worker: `postgresql+psycopg://fiveu:fiveu@postgres:5432/fiveu`

## Daily loop

```sh
make setup
make dev
make migrate    # optional; api already runs alembic on start
make demo       # simulated; no live email
make logs
make down       # docker compose down  — volumes kept
```

## Reset

Reset processes with:

```sh
docker compose down
```

That is what `make down` runs. It does **not** pass `-v`. The `fiveu_pgdata` volume is kept. Do not `docker compose down -v`, `DROP`, or `TRUNCATE`.

## Demo

`APP_MODE=demo` and `LIVE_ACTIONS_ENABLED=false`. `POST http://127.0.0.1:8000/internal/demo/run` loads `fixtures/scenarios/demo-happy-path.json`. No live mailbox send.

## Common failures

**Port in use.** Something else bound `5432`, `8000`, or `5173`. `lsof -nP -iTCP:8000 -sTCP:LISTEN` then stop that process. Compose binds `127.0.0.1` only; a `0.0.0.0` listener still conflicts.

**Migrations.** Api command is `alembic upgrade head && uvicorn ...`. Worker waits until api is healthy. If migrate fails: `make logs`, then `make migrate` (`docker compose exec api alembic upgrade head`). If compose is down: `cd backend && uv run alembic upgrade head` against host Postgres.

**Missing `.env`.** `compose` `env_file: .env` fails. Run `make setup`. It will not overwrite an existing `.env`.

**Healthcheck.** `GET /health/live` must exist (T1). Until then api never becomes healthy and worker/frontend stay down.

**Frontend / Dockerfiles.** `make setup` and `make dev` need `frontend/package.json` plus `backend/Dockerfile` and `frontend/Dockerfile` (T1/T4). Compose build contexts are `backend/` and `frontend/`.
