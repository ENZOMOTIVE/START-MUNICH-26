import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The only hosts a sandbox process may be configured to talk to. `sandbox.invalid` never
# resolves; `sim` is the simulator container on the drill stack's internal network.
SIMULATOR_HOST = "sim"
SANDBOX_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "sandbox.invalid", SIMULATOR_HOST})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # observe: read the real mailbox, send and book nothing, propose actions that cannot run.
    app_mode: Literal["demo", "live", "sandbox", "observe"] = "demo"
    live_actions_enabled: bool = False
    llm_mode: Literal["fake", "gateway"] = "fake"
    simulation_seed: int = 42
    database_url: str = "postgresql+psycopg://fiveu:fiveu@127.0.0.1:5433/fiveu"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:5173"
    hackathon_api_base: str = "https://api.start-hack.5u.ai"
    hackathon_api_key: str = ""
    team_mailbox: str = "free-candy@hackloop.mail"
    hackathon_docs_url: str = "https://api.start-hack.5u.ai/docs"
    hackathon_portal_url: str = "https://start-hack.5u.ai/portal/ui"
    hackathon_board_url: str = "https://api.start-hack.5u.ai/board"
    hackathon_gateway_base: str = "https://api.start-hack.5u.ai/v1/gateway"
    gateway_openai_key: str = ""
    worker_id: str = Field(default="worker-1")
    job_poll_seconds: float = 0.5
    fixtures_dir: str = ""
    mailbox_poll_seconds: float = 1.5
    router_backend: Literal["keyword", "gateway"] = "keyword"
    router_model: str = "gpt-4.1-mini"
    # The mailbox allows 60 mails a minute per team key; stay under it (live sends only).
    send_min_interval_ms: int = 1100
    # When set, pause, resume and sandbox runs need the header X-Operator-Token.
    operator_token: str = ""
    # Supervised trial: every new request waits, paused, until an operator releases it.
    hold_new_requests: bool = False
    dg_requirements_file: str = ""  # default: app/workflows/data/dg_requirements.json
    # Bounded live run (app/platform/runlimits.py). With RUN_LIMITS=true nothing leaves for a
    # thread an operator has not admitted, and every external action is counted against these.
    run_limits: bool = False
    run_minutes: int = 60
    run_max_rfqs: int = 10
    run_max_mails: int = 40
    run_max_bookings: int = 10
    run_max_model_calls: int = 10
    run_max_tokens_in: int = 20000
    run_max_tokens_out: int = 5000
    run_max_spend_usd: float = 1.0
    # One model cross-check of the extracted fields per admitted request (LLM_MODE=gateway).
    # Prices are USD per 1M tokens for EXTRACTION_MODEL; 0 means unverified: no call is made.
    # Door pickup: how many listed truckers of the zone are asked, and how long to wait for
    # all of them before pricing with the bids on file (0: price on the first usable bid).
    pickup_max_truckers: int = 3
    pickup_wait_seconds: int = 0
    extraction_model: str = "gpt-4o-mini"
    model_price_in_usd: float = 0.0
    model_price_out_usd: float = 0.0

    @model_validator(mode="after")
    def _observe_is_read_only(self):
        if self.app_mode != "observe":
            return self
        problems = []
        if self.live_actions_enabled:
            problems.append("LIVE_ACTIONS_ENABLED must be false")
        if self.llm_mode != "fake" or self.router_backend != "keyword":
            problems.append("LLM_MODE must be fake and ROUTER_BACKEND keyword (no paid calls)")
        database = urlsplit(self.database_url.replace("+psycopg", "")).path.lstrip("/")
        if "observe" not in database:
            problems.append(f"DATABASE_URL names '{database}'; observation needs its own database")
        if problems:
            raise ValueError("observation mode refused to start: " + "; ".join(problems))
        return self

    @model_validator(mode="after")
    def _sandbox_fails_closed(self):
        if self.app_mode != "sandbox":
            return self
        problems = []
        if self.live_actions_enabled:
            problems.append("LIVE_ACTIONS_ENABLED must be false")
        if self.llm_mode != "fake":
            problems.append("LLM_MODE must be fake")
        if self.router_backend != "keyword":
            problems.append("ROUTER_BACKEND must be keyword")
        for name in ("hackathon_api_base", "hackathon_gateway_base"):
            host = urlsplit(getattr(self, name)).hostname
            if host not in SANDBOX_HOSTS:
                problems.append(f"{name.upper()} points at {host!r}, not a sandbox host")
        for name in ("hackathon_api_key", "gateway_openai_key"):
            value = getattr(self, name)
            if value and not value.startswith("sandbox-"):
                problems.append(f"{name.upper()} is not a synthetic sandbox- credential")
        if problems:
            raise ValueError("sandbox refused to start: " + "; ".join(problems))
        return self

    def live_allowed(self) -> bool:
        return self.app_mode == "live" and self.live_actions_enabled

    def sandbox_simulator(self) -> str | None:
        """Base URL of the simulator container, when this sandbox process runs against one."""
        host = urlsplit(self.hackathon_api_base).hostname
        if self.app_mode == "sandbox" and host == SIMULATOR_HOST:
            return self.hackathon_api_base
        return None

    def fixtures_path(self) -> Path:
        if self.fixtures_dir:
            return Path(self.fixtures_dir)
        for candidate in (
            Path("/fixtures"),
            Path(__file__).resolve().parents[3] / "fixtures",
            Path(__file__).resolve().parents[2] / "fixtures",
        ):
            if candidate.is_dir():
                return candidate
        return Path("/fixtures")


@lru_cache
def get_settings() -> Settings:
    if os.environ.get("APP_MODE", "").strip().lower() == "sandbox":
        # The sandbox reads only its own process environment, never the live `.env`.
        return Settings(_env_file=None)
    return Settings()
