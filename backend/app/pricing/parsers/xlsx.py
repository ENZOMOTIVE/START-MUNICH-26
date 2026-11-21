"""XLSX tariffs (elbflug, kestrel, vega, wayzata). Cells read as stored values, no float formatting games."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from .base import Row, register


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float):
        # repr() is the shortest round-trip string, e.g. 3.73 -> '3.73'; then exact Decimal from that text
        return format(Decimal(repr(v)), "f")
    return str(v).strip()


@register(".xlsx", ".xlsm")
def read_xlsx(path: Path, checksum: str) -> list[Row]:
    import openpyxl
    from openpyxl.utils import get_column_letter

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    rows: list[Row] = []
    for ws in wb.worksheets:
        for r_idx, r in enumerate(ws.iter_rows(values_only=True), 1):
            cells = [_cell(c) for c in r]
            while cells and not cells[-1]:
                cells.pop()
            if not any(cells):
                continue
            first = next((i for i, c in enumerate(cells) if c), 0)
            loc = f"{ws.title}!{get_column_letter(first + 1)}{r_idx}"
            rows.append(Row(cells[first:], path.name, loc, "xlsx"))
    wb.close()
    return rows
