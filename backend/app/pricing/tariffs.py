import json
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import RateCandidateRow, RfqRow
from app.db.repositories import candidates_for

_FALLBACK_CANDIDATES = {
    "source_ref": "fixtures/rates/demo-fra-lax.json",
    "source_version": "demo-2026-11",
    "candidates": [
        {
            "carrier": "Yellowtail Air Freight",
            "carrier_code": "YT",
            "total": "1250.00",
            "currency": "EUR",
            "transit_days": 5,
            "rate_id": "YT-FRA-LAX-001",
            "complete": True,
            "charges": [
                {"code": "FREIGHT", "amount": "1100.00", "currency": "EUR", "unit": "shipment"},
                {"code": "FUEL", "amount": "150.00", "currency": "EUR", "unit": "shipment"},
            ],
        },
        {
            "carrier": "Helix Air Freight",
            "carrier_code": "HX",
            "total": "1580.00",
            "currency": "EUR",
            "transit_days": 3,
            "rate_id": "HX-FRA-LAX-001",
            "complete": True,
            "charges": [
                {"code": "FREIGHT", "amount": "1400.00", "currency": "EUR", "unit": "shipment"},
                {"code": "FUEL", "amount": "180.00", "currency": "EUR", "unit": "shipment"},
            ],
        },
        {
            "carrier": "Gannet Airways",
            "carrier_code": "GN",
            "total": "1420.00",
            "currency": "EUR",
            "transit_days": 4,
            "rate_id": "GN-FRA-LAX-001",
            "complete": True,
            "charges": [
                {"code": "FREIGHT", "amount": "1280.00", "currency": "EUR", "unit": "shipment"},
                {"code": "FUEL", "amount": "140.00", "currency": "EUR", "unit": "shipment"},
            ],
        },
    ],
}


def _fixture_path():
    return get_settings().fixtures_path() / "rates" / "demo-fra-lax.json"


def load_fixture_candidates(db: Session, rfq: RfqRow) -> list[RateCandidateRow]:
    existing = candidates_for(db, rfq.id)
    if existing:
        return existing
    path = _fixture_path()
    if path.is_file():
        data = json.loads(path.read_text())
    else:
        data = _FALLBACK_CANDIDATES
    source_ref = data.get("source_ref", "fixtures/rates/demo-fra-lax.json")
    source_version = data.get("source_version", "demo-2026-11")
    raw_candidates = data.get("candidates", data if isinstance(data, list) else [])
    rows: list[RateCandidateRow] = []
    for item in raw_candidates:
        row = RateCandidateRow(
            rfq_id=rfq.id,
            carrier=item["carrier"],
            carrier_code=item.get("carrier_code"),
            total=Decimal(str(item["total"])),
            currency=item.get("currency", "EUR"),
            transit_days=item.get("transit_days"),
            rate_id=item.get("rate_id"),
            complete=bool(item.get("complete", True)),
            source_ref=item.get("source_ref", source_ref),
            source_version=item.get("source_version", source_version),
            charges=item.get("charges") or [],
            simulated=True,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows
