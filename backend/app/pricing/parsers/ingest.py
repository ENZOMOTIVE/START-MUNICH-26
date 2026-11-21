"""Walk the kit, checksum + version every tariff set, read -> parse -> validate -> approved
LaneRates or
ReviewExceptions. No file is silently skipped: every file in the directory is accounted for in the
report.

    python -m app.pricing.parsers.ingest [--kit DIR] [--store DIR] [--snapshot FILE] [--no-store]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .. import settings as config
from ..money import MoneyError, parse_number
from ..quote_models import ReviewException
from . import docx as _docx  # noqa: F401  (registers readers)
from . import pdf as _pdf  # noqa: F401
from . import txt as _txt  # noqa: F401
from . import xlsx as _xlsx  # noqa: F401
from .base import READERS, LaneRate, ParsedSet, sha256
from .ocr import parse_image_set
from .parser import parse_rows

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
REF_FILES = {"pickup_zones.csv", "carrier-list.csv"}  # reference data, not tariffs
GERMAN_ORIGINS = {"FRA", "MUC", "DUS", "HAM", "STR", "CGN"}
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}


@dataclass
class SetResult:
    set_id: str
    version: str
    carrier: str
    files: list[dict]
    records: list[LaneRate] = field(default_factory=list)
    exceptions: list[ReviewException] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        blocking = [e for e in self.exceptions if e.blocking]
        if self.records and not blocking:
            return "approved"
        if self.records:
            return "partial"
        return "review"

    def to_dict(self) -> dict:
        return {
            "set_id": self.set_id,
            "version": self.version,
            "carrier": self.carrier,
            "status": self.status,
            "files": self.files,
            "methods": self.methods,
            "records": [r.to_dict() for r in self.records],
            "exceptions": [e.to_dict() for e in self.exceptions],
        }


def group_key(p: Path) -> str:
    return re.sub(r"-p\d+$", "", p.stem)


def carrier_hint(set_id: str) -> str:
    base = re.sub(r"-tariff.*$", "", set_id)
    return " ".join(w.capitalize() for w in base.split("-"))


def set_version(set_id: str, checksums: dict[str, str]) -> str:
    h = hashlib.sha256(
        "|".join(f"{n}:{c}" for n, c in sorted(checksums.items())).encode()
    ).hexdigest()
    return f"{set_id}@{h[:12]}"


def parse_date(raw: str) -> tuple[dt.date | None, dt.date | None]:
    """-> (dd/mm reading, mm/dd reading). For unambiguous formats both are the same."""
    s = raw.strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        d = dt.date(int(m[1]), int(m[2]), int(m[3]))
        return d, d
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$", s)  # German dd.mm.yyyy
    if m:
        d = dt.date(int(m[3]), int(m[2]), int(m[1]))
        return d, d
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$", s)
    if m and m[2].lower() in _MONTHS:
        d = dt.date(int(m[3]), _MONTHS[m[2].lower()], int(m[1]))
        return d, d
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])

        def mk(day, mon):
            try:
                return dt.date(y, mon, day)
            except ValueError:
                return None

        return mk(a, b), mk(b, a)
    return None, None


def resolve_validity(vf: str, vt: str) -> tuple[dt.date, dt.date] | str:
    f1, f2 = parse_date(vf)
    t1, t2 = parse_date(vt)
    options = {(f, t) for f, t in ((f1, t1), (f2, t2)) if f and t and f <= t}
    if len(options) == 1:
        return options.pop()
    if not options:
        return f"cannot read validity {vf!r} .. {vt!r}"
    return f"ambiguous validity {vf!r} .. {vt!r} (dd/mm vs mm/dd)"


def _rules(ps: ParsedSet) -> tuple[dict, list[ReviewException]]:
    text = " ".join(" ".join(r.cells) for r in ps.rules_text).lower()
    src = ps.rules_text[0].source_file if ps.rules_text else None
    out, exc = {}, []
    m = re.search(r"1\s*(?:cbm|m3|m³|cubic met(?:re|er))\s*=\s*(\d+(?:[.,]\d+)?)\s*kg", text)
    if m:
        out["divisor"] = parse_number(m.group(1))
    else:
        out["divisor"] = Decimal(config.DEFAULT_DIVISOR_KG_PER_M3)
        exc.append(
            ReviewException(
                ps.set_id,
                "divisor_not_stated",
                "no volume divisor in sheet; using the challenge default 1 m3 = 167 kg",
                src,
                "set",
                blocking=False,
            )
        )
    if "billed on" in text or "billed weight" in text:
        out["surcharge_basis"] = "billed"
    elif re.search(r"surcharges?[^.]*actual weight", text):
        out["surcharge_basis"] = "actual"
    else:
        out["surcharge_basis"] = "billed"
        exc.append(
            ReviewException(
                ps.set_id,
                "surcharge_basis_not_stated",
                "surcharge basis not stated; assuming per kg of billed weight",
                src,
                "set",
                blocking=False,
            )
        )
    if "minimum charge applies to the freight before surcharges" in text:
        out["min_before_surcharges"] = True
    else:
        out["min_before_surcharges"] = True
        exc.append(
            ReviewException(
                ps.set_id,
                "minimum_rule_not_stated",
                "minimum-charge rule not stated; assuming it applies to freight before surcharges",
                src,
                "set",
                blocking=False,
            )
        )
    m = re.search(r"all rates are per kg[^.]*?in ([a-z]{3})", text)
    out["rates_currency"] = m.group(1).upper() if m else None
    return out, exc


def validate(
    ps: ParsedSet, version: str, files: list[str]
) -> tuple[list[LaneRate], list[ReviewException]]:
    exc = list(ps.exceptions)
    src = files[0] if files else None

    def block_set(reason, detail):
        exc.append(ReviewException(ps.set_id, reason, detail, src, "set", blocking=True))

    carrier = ps.carrier
    if not carrier:
        carrier = carrier_hint(ps.set_id)
        exc.append(
            ReviewException(
                ps.set_id,
                "carrier_from_filename",
                f"carrier name not found in sheet; using filename: {carrier}",
                src,
                "set",
                blocking=False,
            )
        )
    rules, rexc = _rules(ps)
    exc += rexc
    currency = ps.currency.value if ps.currency else None
    if not currency or not re.fullmatch(r"[A-Z]{3}", str(currency)):
        block_set("currency_missing", f"no readable currency (got {currency!r})")
        currency = None
    elif rules["rates_currency"] and rules["rates_currency"] != currency:
        block_set(
            "currency_conflict",
            f"header says {currency}, rules text says {rules['rates_currency']}",
        )
        currency = None
    validity = None
    if not ps.valid_from or not ps.valid_to:
        block_set("validity_missing", "valid from / valid to not readable")
    else:
        v = resolve_validity(str(ps.valid_from.value), str(ps.valid_to.value))
        if isinstance(v, str):
            block_set("validity_unreadable", v)
        else:
            validity = v
    if currency is None or validity is None:
        return [], exc

    blocked_lanes = {
        (e.key or "").split(":")[0] for e in exc if e.blocking and e.scope in ("lane", "cell")
    }
    all_breaks = sorted({b for ln in ps.lanes.values() for b in ln.breaks})
    records = []
    for key, ln in sorted(ps.lanes.items()):
        missing = []
        if ln.min_charge is None:
            missing.append("min_charge")
        if ln.fuel_per_kg is None:
            missing.append("fuel_per_kg")
        if ln.security_per_kg is None:
            missing.append("security_per_kg")
        if 0 not in ln.breaks or len(ln.breaks) < 2:
            missing.append("breaks")
        if missing:
            exc.append(
                ReviewException(
                    ps.set_id,
                    "lane_incomplete",
                    f"{key}: missing {', '.join(missing)}",
                    src,
                    "lane",
                    key,
                    blocking=True,
                )
            )
            continue
        if key in blocked_lanes:
            continue
        nums = [ln.min_charge.value, ln.fuel_per_kg.value, ln.security_per_kg.value] + [
            f.value for f in ln.breaks.values()
        ]
        if any(n < 0 for n in nums) or any(
            f.value <= 0 or f.value > 100 for f in ln.breaks.values()
        ):
            exc.append(
                ReviewException(
                    ps.set_id,
                    "implausible_value",
                    f"{key}: rate out of range",
                    src,
                    "lane",
                    key,
                    blocking=True,
                )
            )
            continue
        brs = sorted((b, f.value) for b, f in ln.breaks.items())
        if any(brs[i + 1][1] > brs[i][1] for i in range(len(brs) - 1)):
            exc.append(
                ReviewException(
                    ps.set_id,
                    "rate_rises_with_weight",
                    f"{key}: a higher break has a higher rate {brs}",
                    src,
                    "lane",
                    key,
                    blocking=False,
                )
            )
        if ln.transit_days is None:
            exc.append(
                ReviewException(
                    ps.set_id,
                    "transit_missing",
                    f"{key}: no transit time",
                    src,
                    "lane",
                    key,
                    blocking=False,
                )
            )
        if ln.origin not in GERMAN_ORIGINS:
            exc.append(
                ReviewException(
                    ps.set_id,
                    "non_german_origin",
                    f"{key}: origin not a German airport",
                    src,
                    "lane",
                    key,
                    blocking=False,
                )
            )
        ev = {
            "min_charge": ln.min_charge.evidence,
            "fuel_per_kg": ln.fuel_per_kg.evidence,
            "security_per_kg": ln.security_per_kg.evidence,
            **{f"break_{b}": f.evidence for b, f in ln.breaks.items()},
        }
        if ln.transit_days:
            ev["transit_days"] = ln.transit_days.evidence
        ev["currency"] = ps.currency.evidence
        ev["validity"] = ps.valid_from.evidence
        records.append(
            LaneRate(
                set_id=ps.set_id,
                version=version,
                carrier=carrier,
                origin=ln.origin,
                destination=ln.destination,
                currency=currency,
                transit_days=ln.transit_days.value if ln.transit_days else None,
                min_charge=ln.min_charge.value,
                breaks=tuple(brs),
                fuel_per_kg=ln.fuel_per_kg.value,
                security_per_kg=ln.security_per_kg.value,
                divisor_kg_per_m3=rules["divisor"],
                surcharge_basis=rules["surcharge_basis"],
                min_before_surcharges=rules["min_before_surcharges"],
                valid_from=validity[0],
                valid_to=validity[1],
                source_files=tuple(files),
                evidence=ev,
            )
        )
    if all_breaks and records:
        pass
    return records, exc


def ingest_group(set_id: str, paths: list[Path]) -> SetResult:
    paths = sorted(paths)
    checksums = {p.name: sha256(p) for p in paths}
    version = set_version(set_id, checksums)
    files = [
        {"name": p.name, "checksum": checksums[p.name], "bytes": p.stat().st_size} for p in paths
    ]
    try:
        if all(p.suffix.lower() in IMAGE_SUFFIXES for p in paths):
            ps = parse_image_set(paths, set_id, checksums)
        else:
            rows = []
            for p in paths:
                reader = READERS.get(p.suffix.lower())
                if reader is None:
                    raise MoneyError(f"no reader for {p.suffix}")
                rows += reader(p, checksums[p.name])
            ps = parse_rows(rows, set_id, checksums)
    except Exception as e:  # unreadable file -> visible exception, never a crash or a silent skip
        res = SetResult(set_id, version, carrier_hint(set_id), files)
        res.exceptions.append(
            ReviewException(
                set_id,
                "unreadable_file",
                f"{type(e).__name__}: {e}",
                ", ".join(checksums),
                "set",
                blocking=True,
            )
        )
        return res
    records, exc = validate(ps, version, [p.name for p in paths])
    carrier = records[0].carrier if records else (ps.carrier or carrier_hint(set_id))
    return SetResult(set_id, version, carrier, files, records, exc, sorted(ps.methods))


def discover(kit: Path) -> tuple[dict[str, list[Path]], list[dict]]:
    """-> ({set_id: files}, [ignored file notes]). Every file is either a tariff or explicitly
    listed."""
    groups: dict[str, list[Path]] = defaultdict(list)
    other = []
    for p in sorted(kit.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.name in REF_FILES:
            other.append({"file": p.name, "role": "reference_data"})
        elif "tariff" in p.name.lower():
            groups[group_key(p)].append(p)
        else:
            other.append({"file": p.name, "role": "unrecognised (not ingested; review)"})
    return dict(groups), other


def ingest(kit: Path | None = None) -> tuple[list[SetResult], list[dict]]:
    kit = kit or config.KIT_DIR
    groups, other = discover(kit)
    return [ingest_group(sid, ps) for sid, ps in sorted(groups.items())], other


def report(results: list[SetResult], other: list[dict]) -> str:
    out = ["TARIFF INGESTION", "=" * 90]
    for r in results:
        blk = [e for e in r.exceptions if e.blocking]
        warn = [e for e in r.exceptions if not e.blocking]
        out.append(
            f"[{r.status:8}] {r.carrier:<24} {len(r.records):>2} lanes  {len(blk)} blocking / "
            f"{len(warn)} non-blocking exc  via {'+'.join(r.methods)}  ({r.version})"
        )
        for e in blk[:12]:
            out.append(f"      BLOCK {e.reason}: {e.detail[:140]}")
        reasons = defaultdict(int)
        for e in warn:
            reasons[e.reason] += 1
        if reasons:
            out.append("      note  " + ", ".join(f"{k} x{v}" for k, v in sorted(reasons.items())))
    for o in other:
        out.append(f"[other   ] {o['file']}: {o['role']}")
    out.append(
        f"TOTAL approved lanes: {sum(len(r.records) for r in results)} "
        f"across {len(results)} tariff sets, "
        f"{sum(len(r.files) for r in results)} files"
    )
    return "\n".join(out)


def write_snapshot(results: list[SetResult], other: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "kind": "pricing.tariff_snapshot",
        "sets": [r.to_dict() for r in results],
        "other_files": other,
    }
    path.write_text(json.dumps(data, indent=1, sort_keys=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kit", type=Path, default=config.KIT_DIR)
    ap.add_argument("--store", type=Path, default=config.STORE_DIR)
    ap.add_argument("--snapshot", type=Path, default=config.SNAPSHOT)
    ap.add_argument("--no-store", action="store_true")
    a = ap.parse_args(argv)
    results, other = ingest(a.kit)
    print(report(results, other))
    if not a.no_store:
        from .store import TariffStore

        st = TariffStore(a.store)
        for r, paths in zip(results, [discover(a.kit)[0][r.set_id] for r in results]):
            new = st.commit(r, paths)
            print(f"  store {r.set_id}: {'NEW version ' if new else 'unchanged '}{r.version}")
    write_snapshot(results, other, a.snapshot)
    print(f"snapshot -> {a.snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
