ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
COMPOSE := docker compose -f "$(ROOT)/compose.yaml" --project-directory "$(ROOT)"
SANDBOX := docker compose -f "$(ROOT)/compose.sandbox.yaml" --project-directory "$(ROOT)"
SANDBOX_DB := postgresql+psycopg://fiveu_sandbox:fiveu_sandbox@127.0.0.1:5434/fiveu_sandbox

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

# Tests commit rows, so they run on the sandbox database only (127.0.0.1:5434), never on the
# application database. tests/conftest.py refuses any database that is not a sandbox or test one.
test:
	$(SANDBOX) up -d --wait postgres
	cd "$(ROOT)/backend" && DATABASE_URL=$(SANDBOX_DB) APP_MODE=demo LIVE_ACTIONS_ENABLED=false \
		uv run --group parsers pytest -q

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

# ---------------------------------------------------------------------------------------------
# Sandbox: own compose project, database (127.0.0.1:5434), volume and synthetic settings.
# Nothing here reads .env or can reach a real service. See docs/sandbox.md.
# ---------------------------------------------------------------------------------------------
.PHONY: sandbox-up sandbox-check sandbox-ui simulate verify sandbox-down sandbox-reset

SANDBOX := docker compose -f "$(ROOT)/compose.sandbox.yaml" --project-directory "$(ROOT)"
SCENARIO ?= all
SEED ?= 42
SANDBOX_DB := postgresql+psycopg://fiveu_sandbox:fiveu_sandbox@127.0.0.1:5434/fiveu_sandbox
SANDBOX_ENV := APP_MODE=sandbox LIVE_ACTIONS_ENABLED=false LLM_MODE=fake SIMULATION_SEED=$(SEED) \
	ROUTER_BACKEND=keyword DATABASE_URL=$(SANDBOX_DB) HACKATHON_API_BASE=https://sandbox.invalid \
	HACKATHON_GATEWAY_BASE=https://sandbox.invalid/v1/gateway \
	HACKATHON_API_KEY=sandbox-not-a-real-key GATEWAY_OPENAI_KEY=

sandbox-up:
	$(SANDBOX) up --build -d --wait
	@echo "sandbox API      http://127.0.0.1:8100/sandbox/status"
	@echo "sandbox database 127.0.0.1:5434 (fiveu_sandbox)"
	@echo "sandbox console  run 'make sandbox-ui', then open http://127.0.0.1:5174/sandbox"

# Fails unless the running API is in sandbox mode with the network guard installed.
sandbox-check:
	@curl -sf http://127.0.0.1:8100/sandbox/status | python3 -c "import json,sys; s=json.load(sys.stdin); print(json.dumps(s, indent=2)); ok = s['app_mode']=='sandbox' and not s['live_actions_enabled'] and s['llm_mode']=='fake' and s['network_guard_installed']; sys.exit(0 if ok else 1)"
	@curl -s -o /dev/null -w "demo trigger in sandbox: HTTP %{http_code} (403 expected)\n" -X POST http://127.0.0.1:8100/internal/demo/run

sandbox-ui:
	cd "$(ROOT)/frontend" && VITE_API_URL=http://127.0.0.1:8100 pnpm dev --host 127.0.0.1 --port 5174 --strictPort

# make simulate SCENARIO=dg_missing_docs SEED=42   (SCENARIO=all runs every scenario)
simulate:
	cd "$(ROOT)/backend" && env $(SANDBOX_ENV) uv run python -m app.sandbox.runner \
		--scenario $(SCENARIO) --seed $(SEED) --out "$(ROOT)/reports"

# Everything: lint, the full backend suite on the sandbox database, every scenario with a
# report in reports/, and the frontend typecheck and build.
verify:
	cd "$(ROOT)/backend" && uv run ruff check .
	cd "$(ROOT)/backend" && DATABASE_URL=$(SANDBOX_DB) APP_MODE=demo LIVE_ACTIONS_ENABLED=false \
		uv run --group parsers pytest -q
	$(MAKE) simulate SCENARIO=all SEED=$(SEED)
	cd "$(ROOT)/frontend" && pnpm typecheck && pnpm build

# Stops the sandbox. The database volume is kept.
sandbox-down:
	$(SANDBOX) down

# Deletes ONLY the sandbox database volume (fiveu-sandbox_fiveu_sandbox_pgdata).
# Run it yourself, and only with: make sandbox-reset CONFIRM=sandbox
sandbox-reset:
	@test "$(CONFIRM)" = "sandbox" || (echo "refusing: run 'make sandbox-reset CONFIRM=sandbox'"; exit 1)
	$(SANDBOX) down -v
