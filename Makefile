ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COMPOSE := docker compose -f "$(ROOT)/compose.yaml" --project-directory "$(ROOT)"

.PHONY: setup dev migrate demo test lint typecheck quality-data logs down openapi

setup:
	if [ ! -f "$(ROOT)/.env" ]; then cp "$(ROOT)/.env.example" "$(ROOT)/.env"; else echo "$(ROOT)/.env exists"; fi
	cd "$(ROOT)/backend" && uv sync
	cd "$(ROOT)/frontend" && pnpm install

dev:
	if [ ! -f "$(ROOT)/.env" ]; then cp "$(ROOT)/.env.example" "$(ROOT)/.env"; fi
	$(COMPOSE) up --build -d

# Prefer compose exec while the api container is up (migrations also run on api start).
# If compose is down, from a host with Postgres on 127.0.0.1:5433:
#   cd "$(ROOT)/backend" && uv run alembic upgrade head
migrate:
	$(COMPOSE) exec api alembic upgrade head

demo:
	curl -sf -X POST http://127.0.0.1:8000/internal/demo/run

test:
	$(COMPOSE) up -d postgres
	@i=0; \
	until $(COMPOSE) exec -T postgres pg_isready -U fiveu -d fiveu; do \
	  i=$$((i+1)); \
	  if [ $$i -gt 30 ]; then echo "postgres did not become ready"; exit 1; fi; \
	  sleep 1; \
	done
	cd "$(ROOT)/backend" && DATABASE_URL=postgresql+psycopg://fiveu:fiveu@127.0.0.1:5433/fiveu APP_MODE=demo LIVE_ACTIONS_ENABLED=false uv run pytest -q

lint:
	cd "$(ROOT)/backend" && uv run ruff check .

# Frontend typecheck only. Python has no mypy/pyright target; use `make lint` (ruff).
typecheck:
	cd "$(ROOT)/frontend" && pnpm typecheck
	@echo "Backend typecheck skipped (no extra Python typechecker). Use make lint."

# T5 data gate. It validates fixtures and starter-kit consistency without parsing tariffs.
# Use --strict only after T2 has resolved any reported source-data discrepancies.
quality-data:
	cd "$(ROOT)/backend" && uv run python scripts/check_demo_data.py

logs:
	$(COMPOSE) logs -f --tail=100

# Do not pass -v. fiveu_pgdata must survive `make down`.
down:
	$(COMPOSE) down

openapi:
	cd "$(ROOT)/backend" && uv run python scripts/export_openapi.py > "$(ROOT)/frontend/openapi.json"
	cd "$(ROOT)/frontend" && pnpm openapi
