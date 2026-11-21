"""Decimal helpers. No float ever enters a price: every input goes through D()."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")
DEFAULT_ROUNDING = ROUND_HALF_UP

_LB = Decimal("0.45359237")  # exact, international pound
_FT3 = Decimal("0.028316846592")  # exact, (0.3048 m)^3
_IN3 = Decimal("0.000016387064")  # exact, (0.0254 m)^3


class MoneyError(ValueError):
    pass


def D(v) -> Decimal:
    """Strict Decimal conversion. Floats are refused (they carry binary noise)."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise MoneyError(f"bool is not a number: {v!r}")
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        raise MoneyError(f"float refused in pricing ({v!r}); pass a str or Decimal")
    if isinstance(v, str):
        return parse_number(v)
    raise MoneyError(f"not a number: {v!r}")


_NUM_RE = re.compile(r"^[+-]?\d+(?:[.,]\d+)?$")


def parse_number(s: str) -> Decimal:
    """'EUR 1,234.50' / '1.234,50' / '75,00' / '3.73' -> Decimal. Raises MoneyError if ambiguous."""
    t = s.strip().replace("\u00a0", " ")
    t = re.sub(r"^(EUR|USD|GBP|CHF|€|\$)\s*", "", t, flags=re.I)
    t = re.sub(r"\s*(EUR|USD|GBP|CHF|€)$", "", t, flags=re.I).replace(" ", "")
    if "," in t and "." in t:
        # the last separator is the decimal one
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    if not _NUM_RE.match(t):
        raise MoneyError(f"not a clean number: {s!r}")
    try:
        return Decimal(t)
    except InvalidOperation as e:  # pragma: no cover
        raise MoneyError(f"not a number: {s!r}") from e


def q2(x: Decimal, rounding: str = DEFAULT_ROUNDING) -> Decimal:
    return D(x).quantize(CENT, rounding=rounding)


def fx_to_eur(amount: Decimal, currency: str, eur_per_unit: Decimal | None) -> Decimal:
    """Convert using an FX quoted as EUR per 1 unit of `currency` (the portal's convention)."""
    if currency.upper() == "EUR":
        return D(amount)
    if eur_per_unit is None:
        raise MoneyError(f"no FX rate for {currency}")
    fx = D(eur_per_unit)
    if fx <= 0:
        raise MoneyError(f"invalid FX {fx} for {currency}")
    return D(amount) * fx


# ---------------- units ----------------
def to_kg(value, unit: str = "kg") -> Decimal:
    u = unit.strip().lower().rstrip(".")
    v = D(value)
    if u in ("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"):
        return v
    if u in ("lb", "lbs", "pound", "pounds"):
        return v * _LB
    if u in ("t", "tonne", "tonnes", "ton", "metric ton"):
        return v * 1000
    if u in ("g", "gram", "grams"):
        return v / 1000
    raise MoneyError(f"unknown weight unit {unit!r}")


def to_m3(value, unit: str = "m3") -> Decimal:
    u = unit.strip().lower().replace("³", "3").replace(" ", "")
    v = D(value)
    if u in ("m3", "cbm", "cubicmetre", "cubicmeter", "cubicmetres", "cubicmeters"):
        return v
    if u in ("cm3", "ccm"):
        return v / Decimal(1_000_000)
    if u in ("l", "litre", "liter", "litres", "liters", "dm3"):
        return v / 1000
    if u in ("ft3", "cuft", "cbf"):
        return v * _FT3
    if u in ("in3", "cuin"):
        return v * _IN3
    raise MoneyError(f"unknown volume unit {unit!r}")


def volume_from_dims(length, width, height, unit: str = "cm", pieces: int = 1) -> Decimal:
    u = unit.strip().lower()
    factor = {
        "cm": Decimal("0.01"),
        "mm": Decimal("0.001"),
        "m": Decimal(1),
        "in": Decimal("0.0254"),
        "inch": Decimal("0.0254"),
    }.get(u)
    if factor is None:
        raise MoneyError(f"unknown length unit {unit!r}")
    return D(length) * factor * D(width) * factor * D(height) * factor * pieces


def chargeable_weight(gross_kg, volume_m3=0, divisor=167) -> Decimal:
    """max(gross_kg, volume_m3 x divisor). Divisor in kg per m3 (167 unless a tariff states otherwise)."""
    return max(D(gross_kg), D(volume_m3 or 0) * D(divisor))


def dstr(x: Decimal | None) -> str | None:
    """Canonical string for JSON: no exponent, no float."""
    if x is None:
        return None
    return format(D(x).normalize(), "f") if D(x) != 0 else "0"
