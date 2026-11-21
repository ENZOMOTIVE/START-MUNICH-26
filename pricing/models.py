"""Contracts. RateCandidate is the T2 -> T3 handoff: T3 quotes and books from it without re-deriving a price."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .money import dstr, q2

Source = Literal["portal", "tariff", "bid"]


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "to_dict"):
        return v.to_dict()
    return v


@dataclass(frozen=True)
class Evidence:
    """Where a value came from. `locator` is file-format specific (line 12 / Sheet1!C5 / page 1 line 9 / ocr pass)."""
    source_file: str
    checksum: str | None = None
    locator: str | None = None
    raw: str | None = None
    method: str | None = None          # txt | xlsx | pdf | docx | ocr | manual_transcription | portal | bid
    note: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class ReviewException:
    """A visible 'a human must look at this' record. Never replaced by an invented value."""
    set_id: str
    reason: str                         # short machine code, e.g. lane_incomplete, ocr_illegible
    detail: str
    source_file: str | None = None
    scope: str = "set"                  # set | lane | cell | portal | request
    key: str | None = None              # lane key / field
    raw: str | None = None
    blocking: bool = True               # True => the affected records are NOT approved

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass(frozen=True)
class ChargeLine:
    code: str                           # air_freight | fuel | security | air_all_in | pickup | fx_note
    description: str
    amount: Decimal                     # exact (unrounded) amount in `currency`
    currency: str = "EUR"
    basis_kg: Decimal | None = None
    rate: Decimal | None = None         # per kg, when applicable
    included_in_total: bool = True
    evidence: Evidence | None = None

    def to_dict(self) -> dict:
        d = {"code": self.code, "description": self.description, "amount": str(q2(self.amount)),
             "amount_exact": dstr(self.amount), "currency": self.currency,
             "included_in_total": self.included_in_total}
        if self.basis_kg is not None:
            d["basis_kg"] = dstr(self.basis_kg)
        if self.rate is not None:
            d["rate"] = dstr(self.rate)
        if self.evidence is not None:
            d["evidence"] = self.evidence.to_dict()
        return d


@dataclass
class RateCandidate:
    carrier: str
    origin: str
    destination: str
    source: Source
    total: Decimal | None               # all-in EUR incl. pickup, quantized to cents (None if incomplete)
    currency: str = "EUR"
    charges: list[ChargeLine] = field(default_factory=list)
    chargeable_weight_kg: Decimal | None = None
    billed_weight_kg: Decimal | None = None
    transit_days: int | None = None
    rate_id: str | None = None
    valid_until: dt.datetime | None = None
    dataset_version: str | None = None
    source_links: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    carrier_code: str | None = None
    source_total: Decimal | None = None   # carrier price in its own currency, before pickup
    source_currency: str | None = None
    fx_eur_per_unit: Decimal | None = None
    air_total: Decimal | None = None      # air leg only (freight+fuel+security) in EUR, quantized
    pivoted: bool = False
    break_kg: int | None = None
    complete: bool = True
    missing: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # cheapest / fastest after ranking

    @property
    def lane(self) -> tuple[str, str]:
        return (self.origin, self.destination)

    @property
    def service(self) -> dict:
        return {"transit_days": self.transit_days, "tags": list(self.tags)}

    def is_expired(self, now: dt.datetime, margin_s: int = 0) -> bool:
        if self.valid_until is None:
            return False
        return now + dt.timedelta(seconds=margin_s) >= self.valid_until

    def bookable(self, now: dt.datetime, margin_s: int = 30) -> bool:
        """Only a complete portal rate with an unexpired rate_id can be booked by id."""
        return bool(self.complete and self.rate_id and not self.is_expired(now, margin_s))

    def eligible(self, now: dt.datetime, margin_s: int = 30) -> bool:
        return self.complete and self.total is not None and not self.is_expired(now, margin_s)

    def to_dict(self, now: dt.datetime | None = None) -> dict:
        d = {
            "carrier": self.carrier, "carrier_code": self.carrier_code,
            "lane": {"origin": self.origin, "destination": self.destination},
            "source": self.source, "service": self.service,
            "total": str(self.total) if self.total is not None else None, "currency": self.currency,
            "charges": [c.to_dict() for c in self.charges],
            "chargeable_weight_kg": dstr(self.chargeable_weight_kg),
            "billed_weight_kg": dstr(self.billed_weight_kg),
            "break_kg": self.break_kg, "pivoted": self.pivoted,
            "rate_id": self.rate_id,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "dataset_version": self.dataset_version,
            "air_total": str(self.air_total) if self.air_total is not None else None,
            "source_total": str(self.source_total) if self.source_total is not None else None,
            "source_currency": self.source_currency,
            "fx_eur_per_unit": dstr(self.fx_eur_per_unit),
            "complete": self.complete, "missing": list(self.missing),
            "source_links": list(self.source_links), "evidence": _jsonable(self.evidence),
        }
        if now is not None:
            d["bookable"] = self.bookable(now)
            d["eligible"] = self.eligible(now)
        return d
