"""Ingestion over the real starter kit + negative cases. No file may be silently skipped."""

import datetime as dt
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

for _mod in ("openpyxl", "pdfplumber", "docx", "PIL"):  # optional `parsers` dep group (follow-up)
    pytest.importorskip(_mod)

from app.pricing.parsers import ocr  # noqa: E402
from app.pricing.parsers.ingest import ingest, ingest_group, resolve_validity  # noqa: E402
from app.pricing.parsers.store import TariffStore  # noqa: E402

from .conftest import ROOT  # noqa: E402

KIT = ROOT / "starter-kit"
EXPECTED_LANES = {
    "Cobalt Skylift": 21,
    "Danube Wings Cargo": 9,
    "Elbflug Air Freight": 19,
    "Helix Air Freight": 12,
    "Kestrel Airways": 18,
    "Orion Cargo Air": 14,
    "Quetzal Air Freight": 11,
    "Vega Skyfreight": 13,
    "Wayzata Air Cargo": 26,
    "Yellowtail Air Freight": 15,
}


def test_every_file_accounted_for(kit_results):
    results, other = kit_results
    covered = {f["name"] for r in results for f in r.files} | {o["file"] for o in other}
    on_disk = {p.name for p in KIT.iterdir() if p.is_file() and not p.name.startswith(".")}
    assert covered == on_disk
    assert len(results) == 10 and sum(len(r.files) for r in results) == 11


def test_all_sets_approved_or_visible(kit_results):
    results, _ = kit_results
    for r in results:
        assert r.records or any(e.blocking for e in r.exceptions), r.set_id
    got = {r.carrier: len(r.records) for r in results}
    assert got == EXPECTED_LANES


@pytest.mark.parametrize(
    "carrier,lane,field,expected",
    [
        ("Yellowtail Air Freight", "FRA-ORD", "min_charge", "110.00"),  # txt
        ("Kestrel Airways", "CGN-MIA", "min_charge", "76.00"),  # xlsx, 2-row header
        ("Kestrel Airways", "CGN-MIA", "fuel_per_kg", "0.69"),  # xlsx surcharge columns
        ("Vega Skyfreight", "CGN-LOS", "min_charge", "114.48"),  # xlsx destination rows
        ("Helix Air Freight", "CGN-LOS", "min_charge", "76.00"),  # pdf "City (XXX) to ..."
        ("Cobalt Skylift", "CGN-EZE", "min_charge", "79.00"),  # pdf block layout
        ("Orion Cargo Air", "CGN-MIA", "min_charge", "78.00"),  # pdf decimal comma
        ("Quetzal Air Freight", "CGN-DFW", "min_charge", "76.00"),  # docx
        ("Danube Wings Cargo", "FRA-DXB", "min_charge", "138.86"),  # OCR scan
        ("Danube Wings Cargo", "FRA-DXB", "fuel_per_kg", "1.12"),  # OCR p2
    ],
)
def test_parsed_values(lanes, carrier, lane, field, expected):
    assert getattr(lanes[(carrier, lane)], field) == Decimal(expected)


def test_breaks_and_rules(lanes):
    ln = lanes[("Yellowtail Air Freight", "FRA-ORD")]
    assert dict(ln.breaks) == {
        0: Decimal("3.73"),
        45: Decimal("3.32"),
        100: Decimal("2.42"),
        250: Decimal("1.82"),
        500: Decimal("1.64"),
        1000: Decimal("1.46"),
        2000: Decimal("1.14"),
    }
    assert (
        ln.divisor_kg_per_m3 == 167 and ln.surcharge_basis == "billed" and ln.min_before_surcharges
    )
    assert (ln.valid_from, ln.valid_to) == (dt.date(2026, 11, 1), dt.date(2026, 11, 30))
    helix = lanes[("Helix Air Freight", "CGN-LOS")]
    assert dict(helix.breaks)[2000] == Decimal("1.29")  # two labels merged in one pdf header cell
    assert lanes[("Cobalt Skylift", "CGN-EZE")].valid_from == dt.date(
        2026, 11, 1
    )  # 01/11/2026 dd/mm


def test_evidence_present(lanes):
    for ln in lanes.values():
        for k in ("min_charge", "fuel_per_kg", "security_per_kg", "break_0"):
            e = ln.evidence[k]
            assert e.source_file and e.checksum and e.locator, (ln.carrier, ln.key, k)


def test_ocr_evidence(lanes):
    ln = lanes[("Danube Wings Cargo", "CGN-SYD")]
    if ocr.tesseract_available():
        assert ln.evidence["min_charge"].method in ("ocr", "manual_transcription")
    else:
        assert ln.evidence["min_charge"].method == "manual_transcription"


def _copy_kit(tmp_path: Path, names: list[str]) -> Path:
    k = tmp_path / "kit"
    k.mkdir()
    for n in names:
        shutil.copy2(KIT / n, k / n)
    return k


def test_corrupt_file_is_review_exception(tmp_path):
    k = tmp_path / "kit"
    k.mkdir()
    (k / "broken-air-tariff-2026-11.xlsx").write_bytes(b"not a zip")
    results, _ = ingest(k)
    assert len(results) == 1 and not results[0].records
    assert (
        results[0].exceptions[0].reason == "unreadable_file" and results[0].exceptions[0].blocking
    )


def test_missing_surcharge_blocks_lane(tmp_path):
    k = tmp_path / "kit"
    k.mkdir()
    src = (KIT / "yellowtail-air-freight-tariff-2026-11.txt").read_text()
    src = "\n".join(l for l in src.splitlines() if not l.startswith("FRA-ORD:"))
    (k / "yellowtail-air-freight-tariff-2026-11.txt").write_text(src)
    r = ingest(k)[0][0]
    assert "FRA-ORD" not in {x.key for x in r.records} and len(r.records) == 14
    assert any(e.reason == "lane_incomplete" and e.key == "FRA-ORD" for e in r.exceptions)
    assert r.status == "partial"


def test_wrong_value_count_blocks_lane(tmp_path):
    k = tmp_path / "kit"
    k.mkdir()
    src = (KIT / "yellowtail-air-freight-tariff-2026-11.txt").read_text()
    src = src.replace("EUR 3.73  EUR 3.32", "EUR 3.32", 1)  # a cell vanished from FRA-ORD's row
    (k / "yellowtail-air-freight-tariff-2026-11.txt").write_text(src)
    r = ingest(k)[0][0]
    assert "FRA-ORD" not in {x.key for x in r.records}
    assert any(
        e.reason in ("unreadable_row", "lane_incomplete") and e.key == "FRA-ORD"
        for e in r.exceptions
    )


def test_missing_currency_blocks_set(tmp_path):
    k = tmp_path / "kit"
    k.mkdir()
    src = (
        (KIT / "yellowtail-air-freight-tariff-2026-11.txt")
        .read_text()
        .replace("Currency    EUR\n", "")
    )
    src = src.replace(", in EUR.", ".")
    (k / "yellowtail-air-freight-tariff-2026-11.txt").write_text(src)
    r = ingest(k)[0][0]
    assert not r.records and any(e.reason == "currency_missing" for e in r.exceptions)


def test_validity_parsing():
    assert resolve_validity("01/11/2026", "30/11/2026") == (
        dt.date(2026, 11, 1),
        dt.date(2026, 11, 30),
    )
    assert resolve_validity("1 November 2026", "30 November 2026")[1] == dt.date(2026, 11, 30)
    assert "ambiguous" in resolve_validity("01/02/2026", "03/04/2026")
    assert "cannot" in resolve_validity("soon", "later")


def test_manual_transcription_bound_to_checksum():
    real = {
        "danube-wings-cargo-tariff-2026-11-p1.jpg": "dd145e8ed92c7dd03125416245415ed244327ad590f0dc7c27b9c16d2f687146",
        "danube-wings-cargo-tariff-2026-11-p2.jpg": "56647a5fb11ce13b662edab41841628d6fb46c023615bf9f3b25989a83f5ce34",
    }
    assert ocr._load_manual("danube-wings-cargo-tariff-2026-11", real) is not None
    changed = dict(real, **{"danube-wings-cargo-tariff-2026-11-p1.jpg": "0" * 64})
    assert ocr._load_manual("danube-wings-cargo-tariff-2026-11", changed) is None


def test_ocr_unavailable_falls_back_visibly(monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_available", lambda: False)
    paths = sorted(KIT.glob("danube-wings-*.jpg"))
    r = ingest_group("danube-wings-cargo-tariff-2026-11", paths)
    assert len(r.records) == 9  # checksum-bound hand transcription
    assert any(e.reason == "ocr_unavailable" for e in r.exceptions)
    assert all(ln.evidence["min_charge"].method == "manual_transcription" for ln in r.records)


def test_store_versions_keep_old_evidence(tmp_path):
    k = _copy_kit(tmp_path, ["yellowtail-air-freight-tariff-2026-11.txt"])
    st = TariffStore(tmp_path / "store")
    r1 = ingest(k)[0][0]
    assert st.commit(r1, [k / f["name"] for f in r1.files]) is True
    assert (
        st.commit(ingest(k)[0][0], [k / f["name"] for f in r1.files]) is False
    )  # same bytes: no-op
    p = k / "yellowtail-air-freight-tariff-2026-11.txt"
    p.write_text(p.read_text().replace("FRA-ORD: fuel EUR 0.66", "FRA-ORD: fuel EUR 0.70"))
    r2 = ingest(k)[0][0]
    assert r2.version != r1.version and st.commit(r2, [p]) is True
    assert st.versions(r1.set_id) == [r1.version, r2.version]
    old = st.record(r1.set_id, r1.version, "FRA", "ORD")
    new = st.record(r2.set_id, r2.version, "FRA", "ORD")
    assert old.fuel_per_kg == Decimal("0.66") and new.fuel_per_kg == Decimal("0.70")
    assert old.evidence["fuel_per_kg"].checksum != new.evidence["fuel_per_kg"].checksum
    cur = {x.key: x for x in st.current_records()}
    assert cur["FRA-ORD"].fuel_per_kg == Decimal("0.70")


def test_ingest_is_deterministic(kit_results):
    again, _ = ingest(KIT)
    assert [r.to_dict() for r in again] == [r.to_dict() for r in kit_results[0]]
