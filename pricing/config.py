"""Paths and env. Secrets come from the environment (or a local, git-ignored .env), never from code."""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KIT_DIR = Path(os.environ.get("PRICING_KIT_DIR", REPO / "starter-kit"))
STORE_DIR = Path(os.environ.get("PRICING_STORE_DIR", REPO / "var" / "pricing_store"))
SNAPSHOT = REPO / "pricing" / "data" / "tariff_snapshot.json"
MANUAL_DIR = Path(__file__).resolve().parent / "tariffs" / "manual"

DEFAULT_DIVISOR_KG_PER_M3 = 167


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    path = path or REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_settings() -> tuple[str, str]:
    load_dotenv()
    base = os.environ.get("HACK_BASE_URL", "https://api.start-hack.5u.ai")
    key = os.environ.get("HACK_API_KEY", "")
    if not key:
        raise RuntimeError("HACK_API_KEY not set (put it in .env or the environment)")
    return base, key
