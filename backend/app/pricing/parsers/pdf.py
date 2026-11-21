"""PDF tariffs (cobalt-skylift, helix, orion): layout-preserving text, then 2+-space column
split."""

from __future__ import annotations

import shutil
import subprocess
from operator import itemgetter
from pathlib import Path

from .base import Row, register
from .txt import split_lines


@register(".pdf")
def read_pdf(path: Path, checksum: str) -> list[Row]:
    rows: list[Row] = []
    if shutil.which("pdftotext"):
        out = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
        for p_idx, page in enumerate(out.split("\f"), 1):
            rows += split_lines(page, path.name, "pdf:pdftotext", prefix=f"page {p_idx} line")
        return rows
    import pdfplumber
    from pdfplumber.utils import cluster_objects

    with pdfplumber.open(path) as pdf:
        for p_idx, p in enumerate(pdf.pages, 1):
            # Cells come from word gaps: layout text puts one space between dense columns (helix).
            words = p.extract_words(keep_blank_chars=True)
            for i, line in enumerate(cluster_objects(words, itemgetter("top"), 3), 1):
                cells = [w["text"].strip() for w in sorted(line, key=itemgetter("x0"))]
                if cells := [c for c in cells if c]:
                    rows.append(Row(cells, path.name, f"page {p_idx} line {i}", "pdf:pdfplumber"))
    return rows
