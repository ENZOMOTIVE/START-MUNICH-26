"""T2 - rate data & pricing. Output contract: app.contracts.models.RateCandidate (money as strings).

Demo/acceptance path: fixtures.candidates_from_fixtures (via candidates.build_rate_candidates).
Live path: portal calculator (GET /v1/portal/rates) + Decimal tariff engine.
Follow-up (NOT on the T2 acceptance path): parsers/ - full pdf/xlsx/txt/docx/ocr tariff parser.
"""

from app.pricing.calculator import select_offer
from app.pricing.candidates import build_rate_candidates, live_candidates_async
from app.pricing.fixtures import candidates_from_fixtures, load_rate_fixtures
from app.pricing.portal import FakePortal, LivePortal, rate_candidate
from app.pricing.tariffs import load_fixture_candidates

__all__ = [
    "FakePortal",
    "LivePortal",
    "build_rate_candidates",
    "candidates_from_fixtures",
    "live_candidates_async",
    "load_fixture_candidates",
    "load_rate_fixtures",
    "rate_candidate",
    "select_offer",
]
