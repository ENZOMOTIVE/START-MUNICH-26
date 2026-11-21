"""Shared types for tariff readers + the reader registry."""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable

from ..models import Evidence, ReviewException
from ..money import dstr


@dataclass
class Row:
    """One logical row of cells, with where it came from."""
    cells: list[str]
    source_file: str
    locator: str
    method: str

    @property
    def raw(self) -> str:
        return " | ".join(self.cells)


@dataclass
class Field:
    value: object
    evidence: Evidence


@dataclass
class ParsedLane:
    origin: str
    destination: str
    transit_days: Field | None = None
    min_charge: Field | None = None
    breaks: dict[int, Field] = field(default_factory=dict)
    fuel_per_kg: Field | None = None
    security_per_kg: Field | None = None

    @property
    def key(self) -> str:
        return f"{self.origin}-{self.destination}"


@dataclass
class ParsedSet:
    """Reader/parser output for one tariff set, before validation."""
    set_id: str
    carrier: str | None = None
    currency: Field | None = None
    valid_from: Field | None = None
    valid_to: Field | None = None
    rules_text: list[Row] = field(default_factory=list)
    lanes: dict[str, ParsedLane] = field(default_factory=dict)
    exceptions: list[ReviewException] = field(default_factory=list)
    methods: set[str] = field(default_factory=set)

    def lane(self, origin: str, dest: str) -> ParsedLane:
        k = f"{origin}-{dest}"
        if k not in self.lanes:
            self.lanes[k] = ParsedLane(origin, dest)
        return self.lanes[k]


@dataclass(frozen=True)
class LaneRate:
    """An APPROVED tariff lane: every field present, validated and evidenced."""
    set_id: str
    version: str
    carrier: str
    origin: str
    destination: str
    currency: str
    transit_days: int | None
    min_charge: Decimal
    breaks: tuple[tuple[int, Decimal], ...]     # sorted (break_kg, rate_per_kg)
    fuel_per_kg: Decimal
    security_per_kg: Decimal
    divisor_kg_per_m3: Decimal
    surcharge_basis: str                        # billed | actual
    min_before_surcharges: bool
    valid_from: dt.date
    valid_to: dt.date
    source_files: tuple[str, ...]
    evidence: dict

    @property
    def key(self) -> str:
        return f"{self.origin}-{self.destination}"

    def to_dict(self) -> dict:
        return {
            "set_id": self.set_id, "version": self.version, "carrier": self.carrier,
            "origin": self.origin, "destination": self.destination, "currency": self.currency,
            "transit_days": self.transit_days, "min_charge": str(self.min_charge),
            "breaks": {str(b): str(r) for b, r in self.breaks},
            "fuel_per_kg": str(self.fuel_per_kg), "security_per_kg": str(self.security_per_kg),
            "divisor_kg_per_m3": dstr(self.divisor_kg_per_m3), "surcharge_basis": self.surcharge_basis,
            "min_before_surcharges": self.min_before_surcharges,
            "valid_from": self.valid_from.isoformat(), "valid_to": self.valid_to.isoformat(),
            "source_files": list(self.source_files),
            "evidence": {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in self.evidence.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LaneRate":
        ev = {k: (Evidence(**v) if isinstance(v, dict) and "source_file" in v else v)
              for k, v in d.get("evidence", {}).items()}
        return cls(
            set_id=d["set_id"], version=d["version"], carrier=d["carrier"], origin=d["origin"],
            destination=d["destination"], currency=d["currency"], transit_days=d.get("transit_days"),
            min_charge=Decimal(d["min_charge"]),
            breaks=tuple(sorted((int(b), Decimal(r)) for b, r in d["breaks"].items())),
            fuel_per_kg=Decimal(d["fuel_per_kg"]), security_per_kg=Decimal(d["security_per_kg"]),
            divisor_kg_per_m3=Decimal(d["divisor_kg_per_m3"]), surcharge_basis=d["surcharge_basis"],
            min_before_surcharges=d["min_before_surcharges"],
            valid_from=dt.date.fromisoformat(d["valid_from"]), valid_to=dt.date.fromisoformat(d["valid_to"]),
            source_files=tuple(d["source_files"]), evidence=ev)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


Reader = Callable[[Path, str], list[Row]]
READERS: dict[str, Reader] = {}


def register(*suffixes: str):
    def deco(fn: Reader) -> Reader:
        for s in suffixes:
            READERS[s] = fn
        return fn
    return deco
