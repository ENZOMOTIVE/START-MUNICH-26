"""Plain-text tariffs (yellowtail): columns separated by 2+ spaces or tabs."""
from __future__ import annotations

import re
from pathlib import Path

from .base import Row, register

SPLIT = re.compile(r"\s{2,}|\t")


def split_lines(text: str, source: str, method: str, prefix: str = "line") -> list[Row]:
    rows = []
    for i, line in enumerate(text.splitlines(), 1):
        cells = [c.strip() for c in SPLIT.split(line.strip()) if c.strip()]
        if cells:
            rows.append(Row(cells, source, f"{prefix} {i}", method))
    return rows


@register(".txt", ".csv")
def read_txt(path: Path, checksum: str) -> list[Row]:
    return split_lines(path.read_text(encoding="utf-8", errors="strict"), path.name, "txt")
