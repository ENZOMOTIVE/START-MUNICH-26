from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine

router = APIRouter()


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        return JSONResponse({"status": "unready"}, status_code=503)


@router.get("/api/meta")
def meta():
    settings = get_settings()
    return {
        "app_mode": settings.app_mode,
        "live_actions_enabled": settings.live_actions_enabled,
        "demo": settings.app_mode == "demo",
        "sandbox": settings.app_mode == "sandbox",
    }
