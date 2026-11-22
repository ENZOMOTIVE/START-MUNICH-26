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
make quality-data # fixture/source audit; reports issues without changing source tariffs
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

## Live run: human reply voice

Customer mails can go out in a warmer, more human voice for the "+10 most human email" jury pick.
It is off by default. Whoever runs the live stack (a person or a Claude Code session) turns it on
like this. The wording lives in `backend/app/workflows/data/reply_voice.md`, the rules in
`backend/app/workflows/voice.py`. Only English customer mails change. Prices, the booking
instruction, tracking answers, other languages and mails to carriers never do.

**Do this when deploying the live stack:**

1. `git pull` so `main` includes the reply voice (`backend/app/workflows/voice.py` exists).
2. In `compose.live.yaml`, add `REPLY_VOICE: "warm"` to the `x-live-env` block next to
   `LLM_MODE`. If the line is already there, set it to `"warm"`.
3. Set a new `LIVE_TAG` in `.env.live` (the voice text is baked into the image, so a rebuild is
   needed), then rebuild and recreate only the API and worker:
   `docker compose -f compose.live.yaml --env-file .env.live up -d --build api worker`
   Do this right after a round's reply has gone out (rounds open about every 6 minutes). A job
   claimed by a stopped worker is taken back after 60 seconds, so nothing is lost.
4. Check the next quote we send. It should end with "Kind regards," and "FiveU Air Freight
   Desk", start with a greeting like "Hi Anna,", and still show the unchanged `Total:` block and
   the "To book, reply to this email and confirm that you accept." line:
   `docker exec -i fiveu-live-postgres-1 psql -U fiveu_live -d fiveu_live -At -c "select payload->>'body' from action_intents where payload->>'purpose'='first_quote' order by created_at desc limit 1"`
5. Watch the next few rounds. Verdicts and accuracy must not change. If a round is rejected for a
   new reason, switch it off (below) and tell the team.

**Switch it off:** set `REPLY_VOICE: "plain"` (or delete the line) and recreate the API and worker
containers (`up -d api worker`). No rebuild is needed.

**Before the first switch-on:** read the three example mails at the end of `reply_voice.md`
aloud once. If any line sounds like a bot or a press release, reword it there first (the test
`tests/test_voice_checklist.py` lists the phrases to avoid).

**Do not** edit a sentence in `backend/app/workflows/replies.py` without running
`make test`: `tests/test_voice.py` fails when a framing sentence no longer matches, because the
voice would silently stop applying to that mail.
