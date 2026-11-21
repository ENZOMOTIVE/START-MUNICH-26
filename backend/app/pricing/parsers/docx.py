"""DOCX tariffs (quetzal): paragraphs and tables in document order."""

from __future__ import annotations

from pathlib import Path

from .base import Row, register


@register(".docx")
def read_docx(path: Path, checksum: str) -> list[Row]:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    rows: list[Row] = []
    p_i = t_i = 0
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p_i += 1
            t = Paragraph(el, d).text.strip()
            if t:
                rows.append(Row([t], path.name, f"paragraph {p_i}", "docx"))
        elif tag == "tbl":
            t_i += 1
            for r_i, r in enumerate(Table(el, d).rows, 1):
                cells, seen = [], set()
                for (
                    c
                ) in r.cells:  # a merged cell is returned once per grid column: dedupe by identity
                    if id(c._tc) in seen:
                        continue
                    seen.add(id(c._tc))
                    cells.append(c.text.strip())
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    rows.append(Row(cells, path.name, f"table {t_i} row {r_i}", "docx"))
    return rows
