"""Engine/portal output mapped onto the team RateCandidate contract; LivePortal is GET-only and gated."""

import datetime as dt
import os
import uuid
from decimal import Decimal

import httpx
import pytest
from app.contracts.models import RFQ, RateCandidate
from app.core.errors import LiveActionsDisabled
from app.pricing.contract import option_to_contract
from app.pricing.engine import PriceRequest, tariff_candidate
from app.pricing.pickup import PickupQuote, apply_pickup
from app.pricing.portal import FakePortal, LivePortal, rate_candidate

from .conftest import make_lane

NOW = dt.datetime(2026, 11, 21, 15, 0, tzinfo=dt.UTC)
RID = uuid.uuid4()


def _rfq(**kw) -> RFQ:
    d = dict(
        id=RID,
        message_id=uuid.uuid4(),
        thread_id="t",
        origin="FRA",
        destination="ORD",
        weight_kg=Decimal("80"),
        volume_m3=Decimal("0"),
        received_at=NOW,
        expires_at=NOW + dt.timedelta(minutes=6),
    )
    d.update(kw)
    return RFQ(**d)


def test_tariff_option_maps_to_contract_with_string_money():
    lane = make_lane()  # 45 kg break @ 3.00, 100 @ 2.00, fuel .50, sec .10, min 100
    opt = tariff_candidate(lane, PriceRequest("ORD", Decimal("80"), origin="FRA"))
    c = option_to_contract(opt, RID, now=NOW)
    assert isinstance(c, RateCandidate)
    d = c.model_dump(mode="json")
    # pivot: 80 kg @3.00 = 240+48 = 288.00 vs 100 kg @2.00 = 200+60 = 260.00 -> bill 100 kg
    assert d["total"] == "260.00"
    assert [(x["code"], x["amount"]) for x in d["charges"]] == [
        ("air_freight", "200.00"),
        ("fuel", "50.00"),
        ("security", "10.00"),
    ]
    assert d["complete"] is True and d["simulated"] is False  # real tariff data, not a fixture
    assert d["source_version"] == "test@0"
    assert (
        "eligible" not in d
        and "bookable" not in d
        and "lane" not in d
        and "dataset_version" not in d
    )


def test_pickup_added_once_and_missing_pickup_is_incomplete():
    lane = make_lane(origin="FRA")
    req = PriceRequest("ORD", Decimal("80"), origin="FRA", pickup_zone="60")
    opt = tariff_candidate(lane, req)
    pq = PickupQuote("60", "FRA", Decimal("29.75"))
    apply_pickup(opt, "60", pq)
    apply_pickup(opt, "60", pq)  # idempotent
    d = option_to_contract(opt, RID, now=NOW).model_dump(mode="json")
    assert [x["code"] for x in d["charges"]].count("pickup") == 1
    assert d["total"] == "289.75"
    no_truck = tariff_candidate(lane, req)
    apply_pickup(no_truck, "60", None)
    c = option_to_contract(no_truck, RID, now=NOW)
    assert c.complete is False and c.model_dump(mode="json")["total"] == "260.00"


def _portal_rate(valid="2026-11-21T15:15:00Z", total="372.12"):
    return {
        "rate_id": "R-JP-1",
        "airline": "Juniper Cargo Lines",
        "airline_code": "JP",
        "origin": "FRA",
        "destination": "ORD",
        "weight_kg": "80",
        "chargeable_weight_kg": "80",
        "total": total,
        "currency": "EUR",
        "fx_eur_per_usd": None,
        "transit_days": 5,
        "valid_until": valid,
        "dataset_version": "ds-42",
    }


def test_portal_rate_maps_to_contract_not_simulated():
    c = rate_candidate(_portal_rate(), _rfq(), now=NOW)
    d = c.model_dump(mode="json")
    assert d["simulated"] is False and d["complete"] is True
    assert (d["total"], d["rate_id"], d["source_version"], d["carrier_code"]) == (
        "372.12",
        "R-JP-1",
        "ds-42",
        "JP",
    )
    assert d["valid_until"] == "2026-11-21T15:15:00Z"


def test_expired_portal_rate_is_incomplete():
    assert (
        rate_candidate(_portal_rate(valid="2026-11-21T14:59:00Z"), _rfq(), now=NOW).complete
        is False
    )


def test_portal_rate_with_pickup_but_no_truck_price_is_incomplete():
    c = rate_candidate(_portal_rate(), _rfq(pickup_required=True, pickup_zone="60"), now=NOW)
    assert c.complete is False


def test_live_portal_is_gated_in_demo_mode():
    with pytest.raises(LiveActionsDisabled):
        LivePortal().get_rate(_rfq(), "JP")
    with pytest.raises(LiveActionsDisabled):
        LivePortal().book("R-1", "FREE-CANDY-1")


def test_live_portal_issues_documented_get_only():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, dict(req.url.params)))
        return httpx.Response(200, json=_portal_rate())

    http = httpx.Client(base_url="https://portal.test", transport=httpx.MockTransport(handler))
    r = LivePortal(allow_reads=True, http=http).get_rate(_rfq(), "JP")
    assert seen == [
        (
            "GET",
            "/v1/portal/rates",
            {
                "origin": "FRA",
                "destination": "ORD",
                "airline": "JP",
                "weight_kg": "80",
                "volume_cbm": "0",
            },
        )
    ]
    assert r["rate_id"] == "R-JP-1"


def test_fake_portal_is_simulated():
    r = FakePortal().get_rate(_rfq(), "YT")
    assert r["simulated"] is True
    assert rate_candidate(r, _rfq(), now=NOW).simulated is True


@pytest.mark.skipif(
    os.environ.get("T2_LIVE_TEST") != "1", reason="opt-in: T2_LIVE_TEST=1 (1 portal GET)"
)
def test_live_portal_read_smoke():
    lp = LivePortal(allow_reads=True)
    try:
        r = lp.get_rate(
            _rfq(
                origin="CGN", destination="DEL", weight_kg=Decimal("80"), volume_m3=Decimal("0.2")
            ),
            "JP",
        )
    finally:
        lp.close()
    c = rate_candidate(r, _rfq(origin="CGN", destination="DEL"))
    assert c.simulated is False and c.rate_id and Decimal(str(c.total)) > 0
