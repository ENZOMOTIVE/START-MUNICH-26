"""Format-agnostic tariff row parser: list[Row] -> ParsedSet (values + per-field evidence).

Layouts seen in the kit (all handled, anything else becomes a ReviewException):
  * one table, lane in col 0: "CGN-SYD" | "CGN Cologne > EZE Buenos Aires" | "Cologne (CGN) to Lagos (LOS)"
  * "Origin: XXX" sections with destination-only rows
  * break headers "-45/+45/..." | "from 0 kg" | "0 kg+"; header may span two rows; several labels in one cell
  * surcharges as table columns, as "lane: fuel X per kg, security Y per kg" lines, or a Lane/Fuel/Security table
  * block layout: lane header, then Transit / Minimum / from N kg / Fuel / Security lines
  * decimal comma or point, "EUR " prefixes, "D+4" / "2 days" transit

Strictness: a data row must carry exactly the number of values its header promises, no blanks, every cell a
clean number. Otherwise the row is not guessed at: it becomes a lane-scoped ReviewException.
"""
from __future__ import annotations

import re

from ..models import Evidence, ReviewException
from ..money import MoneyError, parse_number
from .base import Field, ParsedSet, Row

_CODE = re.compile(r"\b([A-Z]{3})\b")
_NOT_CODES = {"EUR", "USD", "GBP", "CHF", "KG", "CBM", "AWB", "IATA", "PER"}
_BREAK_IN_CELL = re.compile(r"(?:from\s+)?(\d+)\s*kg\s*\+?", re.I)
_BREAK_SIGNED = re.compile(r"^([+-])\s*(\d+)$")
_SURCH = re.compile(r"^(?P<lane>.+?):\s*fuel\s*(?:[A-Z]{3}\s*)?(?P<fuel>\d+(?:[.,]\d+)?)\s*(?:per\s*kg)?"
                    r"\s*,?\s*security\s*(?:[A-Z]{3}\s*)?(?P<sec>\d+(?:[.,]\d+)?)\s*(?:per\s*kg)?\s*$", re.I)
_TRANSIT = re.compile(r"^(?:D\s*\+\s*)?(\d+)(?:\s*days?)?$", re.I)
_BLOCK_BREAK = re.compile(r"^(?:from\s+)?(\d+)\s*kg\s*\+?$", re.I)


def codes_in(text: str) -> list[str]:
    return [c for c in _CODE.findall(text) if c not in _NOT_CODES]


def break_labels(cells: list[str]) -> list[int]:
    out: list[int] = []
    for c in cells:
        found = _BREAK_IN_CELL.findall(c)
        if found:
            out.extend(int(x) for x in found)
            continue
        m = _BREAK_SIGNED.match(c.strip())
        if m:
            out.append(0 if m.group(1) == "-" else int(m.group(2)))
    return out


def parse_transit(s: str) -> int:
    m = _TRANSIT.match(s.strip())
    if not m:
        raise MoneyError(f"not a transit value: {s!r}")
    return int(m.group(1))


def _block_row(low: str) -> bool:
    return low.startswith(("transit", "minimum", "fuel", "security")) or bool(_BLOCK_BREAK.match(low))


class _Ctx:
    def __init__(self, ps: ParsedSet, checksums: dict[str, str]):
        self.ps, self.checksums = ps, checksums

    def ev(self, row: Row, note: str | None = None) -> Evidence:
        return Evidence(source_file=row.source_file, checksum=self.checksums.get(row.source_file),
                        locator=row.locator, raw=row.raw, method=row.method, note=note)

    def exc(self, reason: str, detail: str, row: Row | None = None, key: str | None = None,
            scope: str = "lane", blocking: bool = True) -> None:
        self.ps.exceptions.append(ReviewException(
            set_id=self.ps.set_id, reason=reason, detail=detail, source_file=row.source_file if row else None,
            scope=scope, key=key, raw=row.raw if row else None, blocking=blocking))

    def put(self, lane, attr: str, value, row: Row, brk: int | None = None) -> None:
        """Set a field; a conflicting second value for the same field is an ambiguity, not an overwrite."""
        f = Field(value, self.ev(row))
        old = lane.breaks.get(brk) if brk is not None else getattr(lane, attr)
        if old is not None and old.value != value:
            name = f"break {brk}" if brk is not None else attr
            self.exc("conflicting_value", f"{lane.key} {name}: {old.value} ({old.evidence.locator}) vs "
                     f"{value} ({row.locator})", row, lane.key)
            return
        if brk is not None:
            lane.breaks[brk] = f
        else:
            setattr(lane, attr, f)


def parse_rows(rows: list[Row], set_id: str, checksums: dict[str, str]) -> ParsedSet:
    ps = ParsedSet(set_id=set_id)
    cx = _Ctx(ps, checksums)
    origin_ctx: str | None = None
    breaks: list[int] = []
    has_fs_cols = False
    fs_table = False
    block = None

    for row in rows:
        cells = [c.strip() for c in row.cells]
        if not any(cells):
            continue
        first = cells[0]
        low = first.lower()
        joined = "  ".join(c for c in cells if c)
        ps.methods.add(row.method)

        if "|" in first and "tariff" in low:
            ps.carrier = first.split("|")[0].strip() or ps.carrier
            continue
        if low == "currency" and len(cells) > 1:
            ps.currency = Field(cells[1].strip().upper(), cx.ev(row)); continue
        if low == "valid from" and len(cells) > 1:
            ps.valid_from = Field(cells[1].strip(), cx.ev(row)); continue
        if low == "valid to" and len(cells) > 1:
            ps.valid_to = Field(cells[1].strip(), cx.ev(row)); continue
        if len(cells) == 1 and len(first) > 40 and not codes_in(first.split(":")[0]) \
                or low.startswith(("all rates", "chargeable weight", "fuel and security", "the minimum")):
            ps.rules_text.append(row); continue
        if low.startswith("origin"):
            cs = codes_in(joined)
            origin_ctx = cs[0] if cs else None
            if not cs:
                cx.exc("unreadable_origin", f"origin header without airport code: {joined}", row, scope="set")
            block = None
            continue
        if low.startswith("surcharges"):
            block = None
            continue

        m = _SURCH.match(joined)
        if m:
            cs = codes_in(m.group("lane"))
            if len(cs) == 1 and origin_ctx:
                cs = [origin_ctx] + cs
            if len(cs) == 2:
                ln = ps.lane(cs[0], cs[1])
                cx.put(ln, "fuel_per_kg", parse_number(m.group("fuel")), row)
                cx.put(ln, "security_per_kg", parse_number(m.group("sec")), row)
            else:
                cx.exc("unreadable_lane", f"surcharge line without a clear lane: {joined}", row)
            continue

        bl = break_labels(cells)
        lows = [c.lower() for c in cells]
        if len(bl) >= 3 and not _block_row(low):
            if bl != sorted(set(bl)) or bl[0] != 0:
                cx.exc("bad_break_header", f"weight breaks not ascending from 0: {bl}", row, scope="set")
            breaks = bl
            has_fs_cols = "fuel" in lows and "security" in lows
            fs_table = False
            block = None
            continue
        if not bl and "fuel" in lows and "security" in lows:
            fs_table = True
            block = None
            continue
        if low in ("lane", "destination") or (not bl and "transit" in lows and "minimum" in lows):
            # first line of a two-row header (breaks follow on the next row)
            block = None
            continue

        # block layout key/value lines
        if block is not None and _block_row(low):
            val = cells[-1] if len(cells) > 1 else ""
            try:
                if low.startswith("transit"):
                    cx.put(block, "transit_days", parse_transit(val), row)
                elif low.startswith("minimum"):
                    cx.put(block, "min_charge", parse_number(val), row)
                elif low.startswith("fuel"):
                    cx.put(block, "fuel_per_kg", parse_number(val), row)
                elif low.startswith("security"):
                    cx.put(block, "security_per_kg", parse_number(val), row)
                else:
                    b = int(_BLOCK_BREAK.match(low).group(1))
                    cx.put(block, "", parse_number(val), row, brk=b)
            except MoneyError as e:
                cx.exc("unreadable_value", f"{block.key}: {e}", row, block.key)
            continue

        cs = codes_in(first)
        if not cs:
            cx.exc("unrecognised_row", f"row not understood, ignored for pricing: {joined}", row,
                   scope="set", blocking=False)
            continue
        if len(cs) == 1:
            if not origin_ctx:
                cx.exc("unreadable_lane", f"destination row without origin context: {joined}", row)
                continue
            cs = [origin_ctx, cs[0]]
        if len(cs) != 2:
            cx.exc("unreadable_lane", f"lane cell has {len(cs)} airport codes: {first}", row)
            continue
        lane = ps.lane(cs[0], cs[1])
        vals = cells[1:]
        while vals and vals[-1] == "":
            vals.pop()
        if not vals:
            block = lane          # block layout lane header
            continue
        block = None
        if "" in vals:
            cx.exc("blank_cell", f"{lane.key}: blank cell inside data row", row, lane.key)
            continue
        try:
            if fs_table:
                if len(vals) != 2:
                    raise MoneyError(f"expected fuel+security (2 values), got {len(vals)}")
                cx.put(lane, "fuel_per_kg", parse_number(vals[0]), row)
                cx.put(lane, "security_per_kg", parse_number(vals[1]), row)
                continue
            if not breaks:
                raise MoneyError("data row before any weight-break header")
            need = 2 + len(breaks) + (2 if has_fs_cols else 0)
            if len(vals) != need:
                raise MoneyError(f"expected {need} values, got {len(vals)}")
            transit = parse_transit(vals[0])
            nums = [parse_number(v) for v in vals[1:]]
        except MoneyError as e:
            cx.exc("unreadable_row", f"{lane.key}: {e}", row, lane.key)
            continue
        cx.put(lane, "transit_days", transit, row)
        cx.put(lane, "min_charge", nums[0], row)
        for i, b in enumerate(breaks):
            cx.put(lane, "", nums[1 + i], row, brk=b)
        if has_fs_cols:
            cx.put(lane, "fuel_per_kg", nums[1 + len(breaks)], row)
            cx.put(lane, "security_per_kg", nums[2 + len(breaks)], row)
    return ps
