"""Sandbox console API. `app.main` registers this router only when APP_MODE=sandbox."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core import netguard
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.sandbox import runner
from app.sandbox.scenarios import SCENARIOS

router = APIRouter(prefix="/sandbox")


class RunRequest(BaseModel):
    scenario: str
    seed: int | None = None


@router.get("/status")
def status():
    settings = get_settings()
    return {
        "app_mode": settings.app_mode,
        "llm_mode": settings.llm_mode,
        "live_actions_enabled": settings.live_actions_enabled,
        "simulation_seed": settings.simulation_seed,
        "network_guard_installed": netguard._installed is not None,
        "blocked_network_attempts": netguard.blocked[-20:],
        "data": "SIMULATED",
    }


@router.get("/scenarios")
def scenarios():
    return [
        {
            "id": s["id"],
            "title": s.get("title", ""),
            "version": s["version"],
            "faults": s.get("faults", {}),
        }
        for s in SCENARIOS.values()
    ]


@router.post("/run")
def run(body: RunRequest):
    # A plain `def`: FastAPI runs it in a worker thread, where the pricing code may use asyncio.
    if body.scenario not in SCENARIOS:
        raise NotFoundError(f"scenario {body.scenario}")
    return runner.run(body.scenario, body.seed)
