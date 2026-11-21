"""DB glue used by app.workflows.rfq: persist T2 RateCandidates as `rate_candidates` rows.

Kept under its scaffold name/signature (`load_fixture_candidates(db, rfq)`) so T1/T3 code does not
change.
Fixtures are preferred (demo); see app.pricing.candidates for the live path.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.contracts.models import RateCandidate
from app.core import clock
from app.core.config import get_settings
from app.db.models import RateCandidateRow, RfqRow
from app.db.repositories import candidates_for
from app.platform import get_adapters

from .candidates import build_rate_candidates


def candidate_row(c: RateCandidate) -> RateCandidateRow:
    return RateCandidateRow(
        rfq_id=c.rfq_id,
        carrier=c.carrier,
        carrier_code=c.carrier_code,
        total=c.total,
        currency=c.currency,
        transit_days=c.transit_days,
        rate_id=c.rate_id,
        complete=c.complete,
        valid_until=c.valid_until,
        source_ref=c.source_ref,
        source_version=c.source_version,
        charges=[ch.model_dump(mode="json") for ch in c.charges],
        # Outside live mode every rate comes from a fixture or the simulator: label it so.
        simulated=c.simulated or not get_settings().live_allowed(),
    )


def load_fixture_candidates(db: Session, rfq: RfqRow, pickup=None) -> list[RateCandidateRow]:
    """`pickup` is a trucker's PickupQuote for a door pickup (not used by demo fixtures)."""
    existing = candidates_for(db, rfq.id)
    if existing:
        return existing
    priced = build_rate_candidates(
        rfq, portal_factory=get_adapters().get("rates"), now=clock.now(), pickup=pickup
    )
    rows = [candidate_row(c) for c in priced]
    for row in rows:
        db.add(row)
    db.flush()
    return rows
