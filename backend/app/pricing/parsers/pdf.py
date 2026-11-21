"""PDF tariffs (cobalt-skylift, helix, orion): layout-preserving text, then 2+-space column split."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import Row, register
from .txt import split_lines


@register(".pdf")
def read_pdf(path: Path, checksum: str) -> list[Row]:
    rows: list[Row] = []
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                             text=True, check=True).stdout
        for p_idx, page in enumerate(out.split("\f"), 1):
            rows += split_lines(page, path.name, "pdf:pdftotext", prefix=f"page {p_idx} line")
        return rows
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        for p_idx, p in enumerate(pdf.pages, 1):
            rows += split_lines(p.extract_text(layout=True) or "", path.name, "pdf:pdfplumber",
                                prefix=f"page {p_idx} line")
    return rows
