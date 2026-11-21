import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]          # repo root (starter-kit/ lives here)

from app.pricing.parsers.base import LaneRate  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def kit_results():
    # Follow-up parser (not on the T2 acceptance path): needs the optional `parsers` dep group.
    for mod in ("openpyxl", "pdfplumber", "docx", "PIL"):
        pytest.importorskip(mod)
    from app.pricing.parsers.ingest import ingest

    results, other = ingest(ROOT / "starter-kit")
    return results, other


@pytest.fixture(scope="session")
def lanes(kit_results):
    """{(carrier, 'ORG-DST'): LaneRate}"""
    return {(r.carrier, r.key): r for s in kit_results[0] for r in s.records}


def make_lane(**kw) -> LaneRate:
    """Synthetic, hand-checkable tariff lane."""
    d = dict(set_id="test", version="test@0", carrier="Test Air", origin="FRA", destination="ORD",
             currency="EUR", transit_days=3, min_charge=Decimal("100.00"),
             breaks=((0, Decimal("4.00")), (45, Decimal("3.00")), (100, Decimal("2.00")), (300, Decimal("1.50"))),
             fuel_per_kg=Decimal("0.50"), security_per_kg=Decimal("0.10"), divisor_kg_per_m3=Decimal(167),
             surcharge_basis="billed", min_before_surcharges=True, valid_from=dt.date(2026, 11, 1),
             valid_to=dt.date(2026, 11, 30), source_files=("test.txt",), evidence={})
    d.update(kw)
    return LaneRate(**d)
