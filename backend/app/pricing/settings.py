"""Paths and env for the pricing package. Secrets come from the environment / git-ignored .env,
never code.

The team's `app.core.config.Settings` is the source of truth (`HACKATHON_API_BASE`,
`HACKATHON_API_KEY`).
`HACK_API_KEY` / `HACK_BASE_URL` are still accepted as a fallback for the standalone CLIs.
"""

from __future__ import annotations

import os
from pathlib import Path

PKG = Path(__file__).resolve().parent  # backend/app/pricing
BACKEND = PKG.parents[1]  # backend/
REPO = PKG.parents[2]  # repo root (absent inside the api container)
DATA_DIR = PKG / "data"
KIT_DIR = Path(os.environ.get("PRICING_KIT_DIR", REPO / "starter-kit"))
STORE_DIR = Path(os.environ.get("PRICING_STORE_DIR", REPO / "var" / "pricing_store"))
SNAPSHOT = DATA_DIR / "tariff_snapshot.json"
MANUAL_DIR = PKG / "parsers" / "manual"

DEFAULT_DIVISOR_KG_PER_M3 = 167


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    for p in [path] if path else [REPO / ".env", BACKEND / ".env"]:
        if not p or not p.exists():
            continue
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_settings() -> tuple[str, str]:
    """(base_url, api_key) for the 5U API. Raises if no key is configured."""
    base, key = "", ""
    try:
        from app.core.config import get_settings

        s = get_settings()
        base, key = s.hackathon_api_base, s.hackathon_api_key
    except Exception:  # pragma: no cover - settings import should not block the CLI
        pass
    load_dotenv()
    base = base or os.environ.get("HACK_BASE_URL", "https://api.start-hack.5u.ai")
    key = key or os.environ.get("HACKATHON_API_KEY") or os.environ.get("HACK_API_KEY", "")
    if not key:
        raise RuntimeError("HACKATHON_API_KEY not set (put it in .env or the environment)")
    return base, key
