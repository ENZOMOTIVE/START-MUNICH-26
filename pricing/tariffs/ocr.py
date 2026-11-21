"""OCR reader for scanned tariffs (danube-wings p1+p2 JPGs).

Confidence model (no guessing):
  1. Run tesseract over several preprocessings of each page (original, 2x, 3x, 2x-binarised).
  2. Parse each pass into per-cell readings; a reading counts only if it matches the strict cell format
     (money '1,23' / '134,00', transit 'N days').
  3. A cell is OCR-confirmed when >= 2 passes agree and no other legible reading ties it.
  4. Cross-check against the hand transcription in manual/<set>.json, which is only trusted for the exact
     image checksums it was made from:
       - OCR-confirmed and matches manual       -> approved (method ocr, manual agrees)
       - OCR-confirmed, no manual               -> approved (method ocr)
       - OCR-confirmed but DISAGREES with manual -> blocking ReviewException (lane not approved)
       - not confirmed, manual has it           -> manual value, non-blocking ReviewException (visible)
       - not confirmed, no manual               -> blocking ReviewException, field left empty
  If tesseract is not installed, every cell falls into the last two cases.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from decimal import Decimal
from pathlib import Path

from ..config import MANUAL_DIR
from ..models import Evidence, ReviewException
from ..money import parse_number
from .base import Field, ParsedSet, Row

MONEY_CELL = re.compile(r"^\d{1,4},\d{2}$")
LANE_RE = re.compile(r"^\s*([A-Za-z]{3})\s*[-–]\s*([A-Za-z]{3})\s+(.*)$")
BREAKS_RE = re.compile(r"from\s*(\d+)\s*kg", re.I)


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _passes(img_path: Path) -> list[tuple[str, str]]:
    """Return [(pass_name, text)]."""
    from PIL import Image, ImageOps
    out = []
    variants = [("orig", None, "6"), ("x2", 2, "6"), ("x3", 3, "6"), ("x2bin", -2, "6"), ("x2psm4", 2, "4")]
    with tempfile.TemporaryDirectory() as td:
        base = Image.open(img_path)
        for name, scale, psm in variants:
            if scale is None:
                p = img_path
            else:
                g = ImageOps.grayscale(base)
                s = abs(scale)
                g = g.resize((g.width * s, g.height * s), Image.LANCZOS)
                if scale < 0:
                    g = g.point(lambda v: 255 if v > 160 else 0)
                p = Path(td) / f"{name}.png"
                g.save(p)
            txt = subprocess.run(["tesseract", str(p), "-", "--psm", psm], capture_output=True,
                                 text=True).stdout
            out.append((name, txt))
    return out


def _parse_pass(text: str) -> tuple[dict, dict]:
    """-> (header{currency, valid_from, valid_to, breaks, rules}, cells{(lane, field): reading})."""
    header: dict = {"rules": []}
    cells: dict = {}
    section = None
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("currency"):
            header["currency"] = s.split(None, 1)[1].strip() if " " in s else ""
            continue
        if low.startswith("valid from"):
            header["valid_from"] = s[len("valid from"):].strip(); continue
        if low.startswith("valid to"):
            header["valid_to"] = s[len("valid to"):].strip(); continue
        if low.startswith(("all rates", "chargeable weight", "fuel and security", "the minimum")):
            header["rules"].append(s); continue
        if low.startswith("lane"):
            b = [int(x) for x in BREAKS_RE.findall(s)]
            if "fuel" in low and "security" in low:
                section = "fs"
            elif len(b) >= 3:
                section, header["breaks"] = "rates", b
            continue
        m = LANE_RE.match(s)
        if not m or section is None:
            continue
        lane = f"{m.group(1).upper()}-{m.group(2).upper()}"
        rest = m.group(3)
        if section == "fs":
            toks = rest.split()
            if len(toks) == 2:
                cells[(lane, "fuel_per_kg")] = toks[0]
                cells[(lane, "security_per_kg")] = toks[1]
            continue
        tm = re.match(r"^(\d+)\s*days?\s+(.*)$", rest)
        if not tm:
            continue
        toks = tm.group(2).split()
        brks = header.get("breaks") or []
        if len(toks) != 1 + len(brks):
            continue      # token count off -> this pass says nothing about this row
        cells[(lane, "transit_days")] = tm.group(1)
        cells[(lane, "min_charge")] = toks[0]
        for b, t in zip(brks, toks[1:]):
            cells[(lane, f"break_{b}")] = t
    return header, cells


def _legible(field: str, reading: str) -> bool:
    if field == "transit_days":
        return reading.isdigit()
    return bool(MONEY_CELL.match(reading))


def _consensus(readings: list[tuple[str, str]], field: str) -> tuple[str | None, list[str]]:
    ok = [r for _, r in readings if _legible(field, r)]
    c = Counter(ok).most_common()
    if not c:
        return None, [r for _, r in readings]
    top, n = c[0]
    if n >= 2 and (len(c) == 1 or c[1][1] < n):
        return top, [r for _, r in readings]
    return None, [r for _, r in readings]


def _load_manual(set_id: str, checksums: dict[str, str]) -> dict | None:
    p = MANUAL_DIR / f"{set_id}.json"
    if not p.exists():
        return None
    m = json.loads(p.read_text())
    bound = m.get("source_checksums") or {}
    if not bound or any(checksums.get(f) != c for f, c in bound.items()) or set(bound) != set(checksums):
        return None   # transcription is for different image bytes: not trusted
    return m


def _manual_value(m: dict | None, lane: str, field: str):
    if not m:
        return None
    ln = m.get("lanes", {}).get(lane)
    if not ln:
        return None
    if field.startswith("break_"):
        v = (ln.get("breaks") or {}).get(field[6:])
    elif field in ("fuel_per_kg", "security_per_kg"):
        v = (ln.get("surcharges") or {}).get(field, ln.get(field))
    else:
        v = ln.get(field)
    return None if v is None else str(v)


def parse_image_set(paths: list[Path], set_id: str, checksums: dict[str, str]) -> ParsedSet:
    ps = ParsedSet(set_id=set_id)
    manual = _load_manual(set_id, checksums)
    files = ", ".join(p.name for p in paths)
    ps.methods.add("ocr:tesseract" if tesseract_available() else "ocr:unavailable")

    readings: dict[tuple[str, str], list[tuple[str, str]]] = {}
    headers: list[tuple[str, dict]] = []
    if tesseract_available():
        for p in sorted(paths):
            for name, text in _passes(p):
                h, cells = _parse_pass(text)
                headers.append((f"{p.name}:{name}", h))
                for k, v in cells.items():
                    readings.setdefault(k, []).append((f"{p.name}:{name}", v))
    else:
        ps.exceptions.append(ReviewException(
            set_id, "ocr_unavailable", "tesseract not installed; scan cannot be OCR-read on this host",
            files, "set", blocking=manual is None))

    def hdr(key: str):
        vals = Counter(h.get(key) for _, h in headers if h.get(key))
        return vals.most_common(1)[0][0] if vals else None

    def ev(method: str, loc: str, raw: str | None, note: str | None = None, src: str = files) -> Evidence:
        return Evidence(source_file=src, checksum=",".join(checksums[k] for k in sorted(checksums)),
                        locator=loc, raw=raw, method=method, note=note)

    # header (carrier is printed on p1; currency/validity must be legible or manual-backed)
    ps.carrier = (manual or {}).get("airline")
    for key, mkey in (("currency", "currency"), ("valid_from", "valid_from"), ("valid_to", "valid_to")):
        v = hdr(key)
        if v:
            setattr(ps, key, Field(v, ev("ocr", f"header:{key}", v)))
        elif manual and manual.get(mkey):
            setattr(ps, key, Field(manual[mkey], ev("manual_transcription", f"header:{key}", None)))
    ps.rules_text = [Row([r], files, "ocr header", "ocr") for r in
                     dict.fromkeys(r for _, h in headers for r in h.get("rules", []))]

    breaks = hdr_breaks = None
    bvals = Counter(tuple(h["breaks"]) for _, h in headers if h.get("breaks"))
    if bvals:
        hdr_breaks = list(bvals.most_common(1)[0][0])
    if hdr_breaks is None and manual:
        any_lane = next(iter(manual.get("lanes", {}).values()), {})
        hdr_breaks = sorted(int(b) for b in any_lane.get("breaks", {}))
    breaks = hdr_breaks or []

    lanes = sorted({k[0] for k in readings} | set((manual or {}).get("lanes", {})))
    fields = ["transit_days", "min_charge"] + [f"break_{b}" for b in breaks] + ["fuel_per_kg", "security_per_kg"]
    for lane in lanes:
        o, d = lane.split("-")
        ln = ps.lane(o, d)
        for f in fields:
            rs = readings.get((lane, f), [])
            ocr_val, raw = _consensus(rs, f)
            man = _manual_value(manual, lane, f)
            raw_s = f"ocr passes: {raw}" if raw else None
            chosen = None
            if ocr_val is not None:
                if man is not None and _num(f, man) != _num(f, ocr_val):
                    ps.exceptions.append(ReviewException(
                        set_id, "ocr_manual_conflict",
                        f"{lane} {f}: OCR reads {ocr_val} but hand transcription says {man}", files, "cell",
                        f"{lane}:{f}", raw_s, True))
                    continue
                chosen = Field(_num(f, ocr_val), ev("ocr", f"{lane}:{f}", raw_s,
                               "manual transcription agrees" if man is not None else None))
            elif man is not None:
                chosen = Field(_num(f, man), ev("manual_transcription", f"{lane}:{f}", raw_s,
                               "OCR not confident; value from checksum-bound hand transcription"))
                ps.exceptions.append(ReviewException(
                    set_id, "ocr_illegible_manual_used",
                    f"{lane} {f}: OCR not confident ({raw or 'no reading'}); using hand transcription {man}",
                    files, "cell", f"{lane}:{f}", raw_s, blocking=False))
            else:
                ps.exceptions.append(ReviewException(
                    set_id, "ocr_illegible", f"{lane} {f}: cannot be read confidently ({raw or 'no reading'})",
                    files, "cell", f"{lane}:{f}", raw_s, True))
                continue
            if f.startswith("break_"):
                ln.breaks[int(f[6:])] = chosen
            else:
                setattr(ln, f, chosen)
    return ps


def _num(field: str, s: str):
    if field == "transit_days":
        return int(s)
    return parse_number(s) if not isinstance(s, Decimal) else s
