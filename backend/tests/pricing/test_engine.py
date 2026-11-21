"""Hand-checked prices. Every expected number below was computed by hand from the sheet rows
quoted."""

import datetime as dt
from decimal import Decimal

import pytest
from app.pricing.engine import PriceRequest, applicable_break, price_lane, tariff_candidate
from app.pricing.pickup import PickupQuote, apply_pickup

from .conftest import make_lane

YT = "Yellowtail Air Freight"
# FRA-ORD  4d  min 110.00 | -45 3.73 | +45 3.32 | +100 2.42 | +250 1.82 | ... ; fuel 0.66 security
# 0.11


def req(dest, kg, m3="0", **kw):
    return PriceRequest(destination=dest, gross_kg=Decimal(kg), volume_m3=Decimal(m3), **kw)


def test_pivot_beats_own_bracket(lanes):
    ln = lanes[(YT, "FRA-ORD")]
    # +45: 80*3.32=265.60 + 80*0.66=52.80 + 80*0.11=8.80 = 327.20 ; +100: 242.00+66.00+11.00 =
    # 319.00
    p = price_lane(ln, "80")
    assert (
        p.total == Decimal("319.00")
        and p.best.break_kg == 100
        and p.best.billed_kg == 100
        and p.pivoted
    )
    no = price_lane(ln, "80", pivot=False)
    assert no.total == Decimal("327.20") and not no.pivoted


def test_pivot_frankfurt_nairobi(lanes):
    # FRA-NBO +100 2.87, +250 2.18, fuel 0.94 sec 0.16 ; 230 kg: 660.10+216.20+36.80=913.10 ; 250:
    # 545+235+40=820
    assert price_lane(lanes[(YT, "FRA-NBO")], "230").total == Decimal("820.00")


def test_minimum_charge_before_surcharges(lanes):
    # MUC-LAX min 82, -45 3.12, fuel 1.17 sec 0.14 ; 10 kg: 31.20 < 82 -> 82 + 11.70 + 1.40 = 95.10
    p = price_lane(lanes[(YT, "MUC-LAX")], "10")
    assert p.total == Decimal("95.10") and p.best.min_applied and not p.pivoted
    assert p.best.fuel == Decimal("11.70")  # surcharges on billed kg, NOT scaled up to the minimum


def test_no_pivot_when_own_bracket_cheapest(lanes):
    # HAM-BOG +100 3.53 = +250 3.53 ; 200 kg: 706.00 + 286.00 + 46.00 = 1038.00
    p = price_lane(lanes[(YT, "HAM-BOG")], "200")
    assert p.total == Decimal("1038.00") and p.best.break_kg == 100


@pytest.mark.parametrize(
    "kg,brk,total,pivoted",
    [
        # exactly on the break -> that break's bracket: 45*3.32=149.40 + 29.70 + 4.95 = 184.05
        ("45", 45, "184.05", False),
        # just under: -45 gives 167.81+29.69+4.95=202.45 ; paying for 45 kg at +45 gives 184.05 ->
        # pivot
        ("44.99", 45, "184.05", True),
        # 100 kg exactly: 242.00 + 66.00 + 11.00 = 319.00
        ("100", 100, "319.00", False),
        # 99.99 kg: +45 -> 331.97+65.99+11.00=408.96 ; pivot +100 -> 319.00
        ("99.99", 100, "319.00", True),
    ],
)
def test_weight_break_boundaries(lanes, kg, brk, total, pivoted):
    p = price_lane(lanes[(YT, "FRA-ORD")], kg)
    assert (p.best.break_kg, p.total, p.pivoted) == (brk, Decimal(total), pivoted)


def test_applicable_break():
    assert applicable_break([0, 45, 100], Decimal("44.999")) == 0
    assert applicable_break([0, 45, 100], Decimal("45")) == 45
    assert applicable_break([0, 45, 100], Decimal("1000")) == 100


def test_volumetric_and_rounding(lanes):
    # 50 kg, 0.6 m3 -> 100.2 kg chargeable (+100): 242.484 -> 242.48, 66.132 -> 66.13, 11.022 ->
    # 11.02
    ln = lanes[(YT, "FRA-ORD")]
    assert price_lane(ln, "50", "0.6").total == Decimal(
        "319.63"
    )  # per-line rounding (portal style)
    assert price_lane(ln, "50", "0.6", rounding="total").total == Decimal(
        "319.64"
    )  # round(319.638)


def test_own_divisor_and_actual_basis():
    ln = make_lane(divisor_kg_per_m3=Decimal(200), surcharge_basis="actual")
    # 50 kg, 0.5 m3 -> 100 kg chargeable at divisor 200 (83.5 at 167); +100 -> 200.00 ; surcharges
    # on 50 kg actual
    p = price_lane(ln, "50", "0.5")
    assert p.chargeable_kg == Decimal("100.0") and p.best.freight == Decimal("200.00")
    assert p.best.fuel == Decimal("25.00") and p.best.security == Decimal("5.00")
    assert p.total == Decimal("230.00")


def test_minimum_vs_pivot_synthetic():
    ln = make_lane()
    # 20 kg: -45 4.00 -> 80 < min 100 -> 100 + 10 + 2 = 112 ; +45: 135+22.5+4.5=162 ; +100:
    # 200+50+10=260
    p = price_lane(ln, "20")
    assert p.total == Decimal("112.00") and p.best.min_applied and p.best.break_kg == 0


def test_non_eur_tariff_is_incomplete():
    c = tariff_candidate(make_lane(currency="USD"), req("ORD", "80", origin="FRA"))
    assert not c.complete and c.total is None and "fx:USD" in c.missing


def test_candidate_charge_lines(lanes):
    c = tariff_candidate(lanes[(YT, "FRA-ORD")], req("ORD", "80", origin="FRA"))
    codes = [x.code for x in c.charges]
    assert codes == ["air_freight", "fuel", "security"]
    assert sum(x.amount for x in c.charges) == Decimal("319.00") == c.total == c.air_total
    assert c.rate_id is None and c.dataset_version.startswith(
        "yellowtail-air-freight-tariff-2026-11@"
    )
    assert c.charges[0].evidence.locator == "line 17" and c.charges[1].evidence.locator == "line 34"


def test_pickup_added_exactly_once(lanes):
    ln = lanes[(YT, "FRA-ORD")]
    r = req("ORD", "80", origin="FRA", pickup_zone="60")
    c = tariff_candidate(ln, r)
    assert (
        not c.complete and c.total is None and "pickup" in c.missing
    )  # never invent a truck price
    pq = PickupQuote("60", "FRA", Decimal("45.50"), trucker="Taunusfracht")
    apply_pickup(c, "60", pq)
    apply_pickup(c, "60", pq)  # idempotent
    assert [x.code for x in c.charges].count("pickup") == 1
    assert c.complete and c.total == Decimal("364.50")  # 319.00 + 45.50


def test_pickup_wrong_airport(lanes):
    c = tariff_candidate(lanes[(YT, "FRA-ORD")], req("ORD", "80", origin="FRA"))
    apply_pickup(
        c, "80", PickupQuote("80", "MUC", Decimal("40"))
    )  # zone 80 = Munich -> MUC, not FRA
    assert not c.complete and any(m.startswith("pickup_airport_mismatch") for m in c.missing)


def test_pickup_fx():
    c = tariff_candidate(make_lane(), req("ORD", "80", origin="FRA"))
    apply_pickup(c, "60", PickupQuote("60", "FRA", Decimal("50.00"), "USD", Decimal("0.90")))
    # 80 kg synthetic: +45 240+40+8=288 ; +100 200+50+10=260 -> 260 + 45.00
    assert c.total == Decimal("305.00")


def test_validity_window(lanes):
    c = tariff_candidate(
        lanes[(YT, "FRA-ORD")], req("ORD", "80", origin="FRA", ship_date=dt.date(2026, 12, 2))
    )
    assert not c.complete and "tariff_not_valid_on_ship_date" in c.missing


def test_determinism(lanes, kit_results):
    ln = lanes[(YT, "FRA-ORD")]
    a = tariff_candidate(ln, req("ORD", "123.4", "0.9", origin="FRA")).to_dict()
    b = tariff_candidate(ln, req("ORD", "123.4", "0.9", origin="FRA")).to_dict()
    assert a == b


def test_fixture_unit_conversion():
    import json
    from pathlib import Path

    d = json.loads((Path(__file__).parent / "fixtures" / "rfq_fra_ord_lb_dims.json").read_text())
    r = PriceRequest.from_fixture(d)
    assert r.gross_kg == Decimal("176.37") * Decimal("0.45359237")  # 80.0000909... kg
    assert r.volume_m3 == Decimal("0.24") and r.origin == "FRA" and r.prefer == "fastest"
    d2 = json.loads(
        (Path(__file__).parent / "fixtures" / "rfq_cgn_del_80kg_pickup.json").read_text()
    )
    r2 = PriceRequest.from_fixture(d2)
    assert (r2.pickup_zone, r2.origin) == ("50", "CGN")
