from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.demo import router as demo_router
from app.api.health import router as health_router
from app.api.sessions import router as sessions_router
from app.core import netguard
from app.core.config import get_settings
from app.core.errors import DomainError, NotFoundError
from app.core.logging import configure_logging

configure_logging()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title="FiveU Sessions")
    origins = {
        settings.frontend_origin,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "localhost:5173",
    }
    application.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.include_router(health_router)
    application.include_router(sessions_router)
    application.include_router(demo_router)
    if settings.app_mode == "sandbox":
        # Sandbox controls do not exist in any other mode, and this process cannot reach out.
        from app.api.sandbox import router as sandbox_router

        netguard.install(settings.database_url, settings.sandbox_simulator())
        application.include_router(sandbox_router)

    @application.exception_handler(NotFoundError)
    async def not_found_handler(_request: Request, exc: NotFoundError):
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc)}},
            status_code=404,
        )

    @application.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError):
        status = 403 if exc.code in {"live_actions_disabled", "demo_mode_only"} else 409
        return JSONResponse(
            {"error": {"code": exc.code, "message": str(exc)}},
            status_code=status,
        )

    return application


app = create_app()
