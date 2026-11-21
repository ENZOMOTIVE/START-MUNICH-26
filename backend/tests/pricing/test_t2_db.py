"""T2 through the shared workflow + DB (needs Postgres, like tests/test_foundation.py)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.db.models import RateCandidateRow
from app.db.repositories import quote_for
from app.platform.mailbox import ingest_message
from app.workflows.demo import run_demo
from app.workflows.rfq import handle_rfq
from sqlalchemy import select


def test_handle_rfq_persists_fixture_candidates_and_quotes_yellowtail(db):
    stamp = uuid.uuid4().hex[:8]
    msg, _ = ingest_message(
        db,
        {
            "external_id": f"t2-{stamp}",
            "thread_id": f"t2-thr-{stamp}",
            "sender": "shipper@example.com",
            "recipient": "desk@example.com",
            "subject": "RFQ FRA-LAX 250 kg",
            "body": "FRA to LAX 250 kg pickup 60311",
            "received_at": datetime.now(UTC),
            "classification": "rfq",
            "source": "test",
            "simulated": True,
            "raw": {
                "simulated": True,
                "rfq": {
                    "origin": "FRA",
                    "destination": "LAX",
                    "weight_kg": "250",
                    "pickup_required": True,
                    "pickup_zone": "60",
                    "service_intent": "cheapest",
                },
            },
        },
    )
    session = handle_rfq(db, msg)
    rows = list(
        db.execute(
            select(RateCandidateRow).where(RateCandidateRow.rfq_id == session.rfq_id)
        ).scalars()
    )
    assert len(rows) == 3 and all(r.complete and r.simulated for r in rows)
    assert all(r.valid_until is not None for r in rows)
    chosen = db.get(RateCandidateRow, quote_for(db, session.rfq_id).candidate_id)
    assert (chosen.carrier, chosen.total, chosen.rate_id) == (
        "Yellowtail Air Freight",
        Decimal("412.50"),
        "R-DEMOYELLOW01",
    )
    assert [(c["code"], c["amount"]) for c in chosen.charges] == [
        ("air_freight", "350.00"),
        ("fuel", "29.25"),
        ("security", "3.50"),
        ("pickup", "29.75"),
    ]


def test_demo_run_selects_yellowtail_41250(db):
    session_id = run_demo(db)
    from app.db.models import SessionRow

    rfq_id = db.get(SessionRow, session_id).rfq_id
    chosen = db.get(RateCandidateRow, quote_for(db, rfq_id).candidate_id)
    assert chosen.carrier == "Yellowtail Air Freight"
    assert chosen.total == Decimal("412.50")
