from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_mode: Literal["demo", "live"] = "demo"
    live_actions_enabled: bool = False
    database_url: str = "postgresql+psycopg://fiveu:fiveu@127.0.0.1:5433/fiveu"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:5173"
    hackathon_api_base: str = "https://api.start-hack.5u.ai"
    hackathon_api_key: str = ""
    gateway_openai_key: str = ""
    worker_id: str = Field(default="worker-1")
    job_poll_seconds: float = 0.5
    fixtures_dir: str = ""

    def live_allowed(self) -> bool:
        return self.app_mode == "live" and self.live_actions_enabled

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
    return Settings()
