import os
from pathlib import Path

os.environ["APP_MODE"] = "demo"
os.environ["LIVE_ACTIONS_ENABLED"] = "false"

import pytest
from alembic import command
from alembic.config import Config
from app.db.session import get_db, get_engine, get_session_factory
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import inspect

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _ensure_schema() -> None:
    engine = get_engine()
    if inspect(engine).has_table("messages"):
        return
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture
def db():
    _ensure_schema()
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
