"""T2 acceptance (docs/tasks/T2.md): the demo RFQ yields three complete simulated candidates from
fixtures;
cheapest is Yellowtail 412.50 EUR = 350.00 + 29.25 + 3.50 + 29.75. No DB, no network."""

import datetime as dt
import json
import shutil
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from app.contracts.models import RateCandidate
from app.core.errors import DomainError
from app.pricing import candidates as cand_mod
from app.pricing.calculator import select_offer
from app.pricing.candidates import build_rate_candidates
from app.pricing.fixtures import candidates_from_fixtures, load_rate_fixtures
from app.pricing.pickup import airport_for_zone

from .conftest import ROOT

FIXTURES = ROOT / "fixtures"
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


def _demo(**over):
    return {**DEMO_RFQ, **over}


def _load(rfq=None, fixtures_dir=FIXTURES, now=NOW):
    return candidates_from_fixtures(rfq or DEMO_RFQ, fixtures_dir=fixtures_dir, now=now)


def test_demo_yields_three_complete_simulated_candidates():
    cs = _load()
    assert len(cs) == 3
    assert all(isinstance(c, RateCandidate) for c in cs)
    assert all(c.complete and c.simulated for c in cs)
    assert {c.rfq_id for c in cs} == {uuid.UUID(DEMO_RFQ["id"])}
    by_code = {c.carrier_code: c for c in cs}
    exp = {
        "YT": ("Yellowtail Air Freight", "412.50", 5, "R-DEMOYELLOW01"),
        "HX": ("Helix Air Freight", "520.00", 3, "R-DEMOHELIX0001"),
        "VG": ("Vega Skyfreight", "480.00", 4, "R-DEMOVEGA000001"),
    }
    for code, (carrier, total, transit, rate_id) in exp.items():
        c = by_code[code]
        dumped = c.model_dump(mode="json")
        assert (c.carrier, dumped["total"], c.transit_days, c.rate_id) == (
            carrier,
            total,
            transit,
            rate_id,
        )
        assert dumped["valid_until"] == "2027-01-01T00:00:00Z"
        assert dumped["currency"] == "EUR"
        assert c.source_ref == "fixtures/rates/demo-fra-lax.json"
        assert c.source_version == "demo-2026-11"


def test_cheapest_is_yellowtail_41250_with_exact_breakdown():
    best = select_offer(_load(), "cheapest")
    d = best.model_dump(mode="json")
    assert d["carrier"] == "Yellowtail Air Freight"
    assert d["total"] == "412.50"
    assert [(ch["code"], ch["amount"], ch["currency"]) for ch in d["charges"]] == [
        ("air_freight", "350.00", "EUR"),
        ("fuel", "29.25", "EUR"),
        ("security", "3.50", "EUR"),
        ("pickup", "29.75", "EUR"),
    ]
    assert sum(Decimal(ch["amount"]) for ch in d["charges"]) == Decimal("412.50")
    assert [ch["code"] for ch in d["charges"]].count("pickup") == 1


def test_fastest_is_helix():
    assert select_offer(_load(), "fastest").carrier_code == "HX"


def test_money_and_weights_are_strings_on_the_wire():
    payload = json.loads(json.dumps([c.model_dump(mode="json") for c in _load()]))
    for c in payload:
        assert (
            isinstance(c["total"], str)
            and c["total"].count(".") == 1
            and len(c["total"].split(".")[1]) == 2
        )
        for ch in c["charges"]:
            assert isinstance(ch["amount"], str) and len(ch["amount"].split(".")[1]) == 2


def test_pickup_zone_60_maps_to_fra():
    assert airport_for_zone("60") == "FRA"
    assert airport_for_zone("80") == "MUC"


def test_pickup_zone_for_other_airport_marks_incomplete():
    cs = _load(_demo(pickup_zone="80"))  # Munich zone, FRA origin
    assert cs and not any(c.complete for c in cs)
    with pytest.raises(DomainError):
        select_offer(cs, "cheapest")


def test_lane_without_fixture_yields_nothing():
    assert _load(_demo(destination="JFK")) == []


def test_build_rate_candidates_prefers_fixtures_in_demo_and_never_hits_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("live pricing must not run in demo mode")

    monkeypatch.setattr(cand_mod, "live_candidates_async", boom)
    cs = build_rate_candidates(DEMO_RFQ, fixtures_dir=FIXTURES, now=NOW)
    assert select_offer(cs, "cheapest").model_dump(mode="json")["total"] == "412.50"


def test_default_fixtures_dir_resolves_repo_fixtures():
    names = [f.path.name for f in load_rate_fixtures()]
    assert "demo-fra-lax.json" in names


# ---- incomplete quotes are visible but never selectable ------------------------------------------
@pytest.fixture
def tmp_fixtures(tmp_path: Path):
    (tmp_path / "rates").mkdir()
    shutil.copy(FIXTURES / "rates" / "demo-fra-lax.json", tmp_path / "rates" / "demo-fra-lax.json")
    return tmp_path


def _edit(root: Path, fn):
    p = root / "rates" / "demo-fra-lax.json"
    d = json.loads(p.read_text())
    fn(d)
    p.write_text(json.dumps(d))


def test_total_not_matching_charges_is_incomplete(tmp_fixtures):
    _edit(tmp_fixtures, lambda d: d["candidates"][0].update(total="400.00"))
    cs = {c.carrier_code: c for c in _load(fixtures_dir=tmp_fixtures)}
    assert cs["YT"].complete is False
    assert select_offer(list(cs.values()), "cheapest").carrier_code == "VG"


def test_expired_fixture_is_incomplete(tmp_fixtures):
    _edit(tmp_fixtures, lambda d: d["candidates"][0].update(valid_until="2026-11-21T14:59:00Z"))
    cs = {c.carrier_code: c for c in _load(fixtures_dir=tmp_fixtures)}
    assert cs["YT"].complete is False


def test_double_pickup_line_is_incomplete(tmp_fixtures):
    def dup(d):
        c = d["candidates"][0]
        c["charges"].append(dict(c["charges"][-1]))
        c["total"] = "442.25"

    _edit(tmp_fixtures, dup)
    cs = {c.carrier_code: c for c in _load(fixtures_dir=tmp_fixtures)}
    assert cs["YT"].complete is False


def test_missing_pickup_line_when_pickup_required_is_incomplete(tmp_fixtures):
    def drop(d):
        c = d["candidates"][0]
        c["charges"] = [x for x in c["charges"] if x["code"] != "pickup"]
        c["total"] = "382.75"

    _edit(tmp_fixtures, drop)
    cs = {c.carrier_code: c for c in _load(fixtures_dir=tmp_fixtures)}
    assert cs["YT"].complete is False
    # same file, RFQ without pickup: the air-only quote is complete
    cs2 = {
        c.carrier_code: c
        for c in _load(_demo(pickup_required=False, pickup_zone=None), fixtures_dir=tmp_fixtures)
    }
    assert cs2["YT"].complete is True


def test_fixture_flagged_incomplete_stays_incomplete(tmp_fixtures):
    _edit(tmp_fixtures, lambda d: d["candidates"][0].update(complete=False))
    cs = {c.carrier_code: c for c in _load(fixtures_dir=tmp_fixtures)}
    assert cs["YT"].complete is False
