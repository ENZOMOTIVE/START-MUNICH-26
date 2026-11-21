"""T3 acceptance (docs/tasks/T3.md): selection rules with evidence, and a replay-safe demo."""

import datetime as dt
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from app.core.errors import DomainError
from app.db.models import ActionIntentRow, BookingRow, RfqRow, SessionRow
from app.pricing.fixtures import candidates_from_fixtures
from app.workflows.selection import choose_offer_with_evidence
from sqlalchemy import select

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
NOW = dt.datetime(2026, 11, 21, 15, 0, tzinfo=dt.UTC)
DEMO_RFQ = {
    "id": str(uuid.uuid4()),
    "origin": "FRA",
    "destination": "LAX",
    "weight_kg": "250",
    "volume_m3": None,
    "pickup_required": True,
    "pickup_zone": "60",
    "service_intent": "cheapest",
}


def test_selection_rules_and_evidence():
    candidates = candidates_from_fixtures(DEMO_RFQ, fixtures_dir=FIXTURES, now=NOW)

    cheapest, evidence = choose_offer_with_evidence(candidates, "cheapest", now=NOW)
    assert (cheapest.carrier_code, Decimal(str(cheapest.total))) == ("YT", Decimal("412.50"))
    assert evidence["rule"] == "minimum_total_among_complete_current_candidates"
    assert len(evidence["considered_candidate_ids"]) == 3

    assert choose_offer_with_evidence(candidates, "fastest", now=NOW)[0].carrier_code == "HX"

    without_yt = [c.model_copy(update={"complete": c.carrier_code != "YT"}) for c in candidates]
    picked, evidence = choose_offer_with_evidence(without_yt, "cheapest", now=NOW)
    assert picked.carrier_code == "VG"
    assert evidence["excluded_incomplete_candidate_ids"] == [str(cheapest.id)]

    after_validity = dt.datetime(2027, 1, 2, tzinfo=dt.UTC)
    with pytest.raises(DomainError) as exc:
        choose_offer_with_evidence(candidates, "cheapest", now=after_validity)
    assert exc.value.code == "offers_expired"


def test_demo_replay_keeps_one_quote_and_one_booking(client, db):
    first = client.post("/internal/demo/run").json()["session_id"]
    assert client.post("/internal/demo/run").json()["session_id"] == first

    rfq = db.get(RfqRow, db.get(SessionRow, uuid.UUID(first)).rfq_id)
    assert (rfq.thread_id, rfq.status) == ("demo-thread", "booked")
    kinds = db.scalars(select(ActionIntentRow.kind).where(ActionIntentRow.rfq_id == rfq.id)).all()
    # `reply` is the booking-confirmed mail; there is still one quote and one booking action.
    assert sorted(kinds) == ["booking", "first_quote", "reply"]
    bookings = db.scalars(select(BookingRow).where(BookingRow.rfq_id == rfq.id)).all()
    assert [(b.status, b.simulated) for b in bookings] == [("confirmed", True)]
