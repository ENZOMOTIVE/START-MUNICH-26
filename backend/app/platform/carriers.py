import csv
from dataclasses import dataclass
from email.utils import parseaddr
from functools import lru_cache
from pathlib import Path

# The backend image has no starter-kit/, so a copy is bundled; the repo file wins when present.
REPO_CARRIER_LIST = Path(__file__).resolve().parents[3] / "starter-kit" / "carrier-list.csv"
BUNDLED_CARRIER_LIST = Path(__file__).resolve().parent / "data" / "carrier-list.csv"


@dataclass(frozen=True)
class Carrier:
    name: str
    kind: str
    email: str


def normalize_address(value: str | None) -> str:
    return parseaddr(value or "")[1].strip().lower()


def carrier_list_path(repo_path: Path = REPO_CARRIER_LIST) -> Path:
    return repo_path if repo_path.is_file() else BUNDLED_CARRIER_LIST


@lru_cache
def load_carriers(path: Path | None = None) -> dict[str, Carrier]:
    source = path or carrier_list_path()
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    carriers = {}
    for row in rows:
        email = normalize_address(row.get("email"))
        if email:
            carriers[email] = Carrier(
                name=(row.get("name") or "").strip(),
                kind=(row.get("kind") or "").strip().lower(),
                email=email,
            )
    return carriers


def carrier_for(address: str | None) -> Carrier | None:
    return load_carriers().get(normalize_address(address))
