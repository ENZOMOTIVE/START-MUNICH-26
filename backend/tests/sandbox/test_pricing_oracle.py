"""Pricing checked against totals worked out by hand from the source tariff.

Source: starter-kit/yellowtail-air-freight-tariff-2026-11.txt, lane FRA-ORD:
  minimum 110.00; per kg 3.73 (<45), 3.32 (45), 2.42 (100), 1.82 (250), 1.64 (500), 1.46 (1000),
  1.14 (2000); fuel 0.66 and security 0.11 per kg of billed weight; 1 cbm = 167 kg;
  the minimum applies to freight before surcharges.
The expected totals below are arithmetic on those numbers, not output of the pricing code.
Each line is rounded half-up to the cent. A shipment may be billed at the next break when that
is cheaper.
"""

import datetime as dt
import re
from decimal import Decimal
from pathlib import Path

import pytest
from app.pricing.contract import option_to_contract
from app.pricing.engine import PriceRequest, price_lane, tariff_candidate
from app.pricing.money import D, MoneyError
from app.pricing.pickup import PickupQuote, apply_pickup
from app.pricing.portal_client import candidate_from_rate
from app.pricing.tariff_candidates import TariffBook
from app.workflows.selection import choose_offer_with_evidence

KIT = Path(__file__).resolve().parents[3] / "starter-kit"
NOW = dt.datetime(2026, 11, 21, 9, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def lane():
    lanes = TariffBook.from_snapshot().lanes("FRA", "ORD")
    return next(x for x in lanes if x.carrier == "Yellowtail Air Freight")


def test_the_lane_matches_the_source_file(lane):
    source = (KIT / "yellowtail-air-freight-tariff-2026-11.txt").read_text()
    row = re.search(r"^FRA-ORD\s+(\d+)\s+EUR ([\d.]+)((?:\s+EUR [\d.]+){7})$", source, re.M)
    rates = re.findall(r"EUR ([\d.]+)", row.group(3))
    assert (row.group(1), row.group(2)) == ("4", "110.00")
    assert rates == ["3.73", "3.32", "2.42", "1.82", "1.64", "1.46", "1.14"]
    assert "FRA-ORD: fuel EUR 0.66 per kg, security EUR 0.11 per kg" in source
    assert lane.min_charge == Decimal("110.00") and lane.transit_days == 4
    assert [str(rate) for _, rate in lane.breaks] == rates
    assert (lane.fuel_per_kg, lane.security_per_kg) == (Decimal("0.66"), Decimal("0.11"))
    assert lane.currency == "EUR"


@pytest.mark.parametrize(
    "kg,cbm,expected,working",
    [
        ("300", "1.0", "777.00", "300 x 1.82 = 546.00, fuel 198.00, security 33.00"),
        ("100", "2.0", "865.06", "volume 334 kg x 1.82 = 607.88, fuel 220.44, security 36.74"),
        ("20", "0.05", "125.40", "74.60 raised to the 110.00 minimum, fuel 13.20, security 2.20"),
        ("250", "0", "647.50", "on the break: 250 x 1.82 = 455.00, fuel 165.00, security 27.50"),
        ("249", "0", "647.50", "own break 794.31; billed as 250 kg it is 647.50"),
        ("45", "0", "184.05", "45 x 3.32 = 149.40, fuel 29.70, security 4.95"),
        ("44", "0", "184.05", "own break 198.00; billed as 45 kg it is 184.05"),
        ("271.25", "0", "702.55", "493.675 -> 493.68, 179.025 -> 179.03, 29.8375 -> 29.84"),
    ],
)
def test_totals_match_hand_calculation(lane, kg, cbm, expected, working):
    assert price_lane(lane, Decimal(kg), Decimal(cbm)).total == Decimal(expected), working


def test_elbflug_fra_dfw_matches_a_calculation_from_its_own_tariff_sheet():
    """FRA-DFW, 300 kg, 1.2 cbm, taken cell by cell from the source spreadsheet.

    Chargeable weight: max(300, 1.2 x 167 = 200.4) = 300 kg, which is exactly the 300 kg break.
    Freight 300 x 1.19 = 357.00 (above the 108.00 minimum); fuel 300 x 0.74 = 222.00;
    security 300 x 0.14 = 42.00; total 621.00. Billing at the 500 kg break would cost
    500 x 0.92 + 500 x 0.88 = 900.00, so it does not apply.
    """
    openpyxl = pytest.importorskip("openpyxl")
    sheet = openpyxl.load_workbook(
        KIT / "elbflug-air-freight-tariff-2026-11.xlsx", data_only=True
    ).active
    rows = [[str(c) for c in row if c is not None] for row in sheet.iter_rows(values_only=True)]
    start = rows.index(["Origin: FRA"])
    end = next(i for i in range(start + 1, len(rows)) if rows[i][:1] and "Origin:" in rows[i][0])
    header = rows[start + 1]
    dfw = next(row for row in rows[start:end] if row[:1] == ["DFW"])
    cell = dict(zip(header, dfw, strict=True))
    assert (cell["Transit"], cell["Minimum"], cell["from 300 kg"]) == ("3", "108,00", "1,19")
    assert cell["from 500 kg"] == "0,92"
    assert ["FRA-DFW: fuel 0,74 per kg, security 0,14 per kg"] in rows

    def money(text):
        return Decimal(text.replace(",", "."))

    kg = max(Decimal("300"), Decimal("1.2") * 167)
    freight = max(money(cell["Minimum"]), kg * money(cell["from 300 kg"]))
    expected = freight + kg * Decimal("0.74") + kg * Decimal("0.14")
    assert (kg, freight, expected) == (Decimal("300"), Decimal("357.00"), Decimal("621.00"))

    elbflug = next(
        x for x in TariffBook.from_snapshot().lanes("FRA", "DFW")
        if x.carrier == "Elbflug Air Freight"
    )
    assert price_lane(elbflug, Decimal("300"), Decimal("1.2")).total == expected


def test_pickup_is_charged_exactly_once_and_required_when_asked(lane):
    request = PriceRequest(
        "ORD", Decimal("300"), Decimal("1.0"), "FRA", "60", "cheapest", NOW.date()
    )
    option = tariff_candidate(lane, request)
    apply_pickup(option, "60", None)
    assert option.complete is False and option.total is None and "pickup" in option.missing
    truck = PickupQuote(zone="60", airport="FRA", cost=Decimal("85.00"))
    for _ in range(2):  # applying twice must not add it twice
        apply_pickup(option, "60", truck)
    assert option.complete and option.total == Decimal("862.00")  # 777.00 + 85.00
    assert [c.code for c in option.charges].count("pickup") == 1


def _portal_rate(**over):
    rate = {
        "rate_id": "R-1",
        "airline": "Delta Echo Cargo",
        "airline_code": "DE",
        "origin": "FRA",
        "destination": "JFK",
        "weight_kg": "200",
        "chargeable_weight_kg": "200.4",
        "total": "501.00",
        "currency": "USD",
        "fx_eur_per_usd": "0.92",
        "transit_days": 2,
        "valid_until": (NOW + dt.timedelta(minutes=15)).isoformat(),
        "dataset_version": "v1",
    }
    rate.update(over)
    return rate


def test_currency_is_converted_in_the_right_direction():
    option = candidate_from_rate(_portal_rate(), 200, Decimal("1.2"), now=NOW)
    assert option.air_total == Decimal("460.92")  # 501.00 USD x 0.92 EUR per USD, not / 0.92
    assert option.source_total == Decimal("501.00") and option.source_currency == "USD"


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"fx_eur_per_usd": None}, "fx:USD"),
        ({"rate_id": None}, "rate_id"),
        ({"valid_until": None}, "valid_until"),
        ({"currency": None}, "currency"),
    ],
)
def test_a_rate_missing_something_required_is_not_complete(change, reason):
    option = candidate_from_rate(_portal_rate(**change), 200, Decimal("1.2"), now=NOW)
    apply_pickup(option, None, None)
    assert reason in option.missing
    contract = option_to_contract(option, __import__("uuid").uuid4(), now=NOW)
    assert contract.complete is False


def test_an_expired_rate_is_not_offered():
    past = (NOW - dt.timedelta(minutes=1)).isoformat()
    option = candidate_from_rate(_portal_rate(valid_until=past), 200, Decimal("1.2"), now=NOW)
    apply_pickup(option, None, None)
    contract = option_to_contract(option, __import__("uuid").uuid4(), now=NOW)
    assert contract.complete is False


def test_floats_are_refused_as_money():
    with pytest.raises(MoneyError):
        D(1.5)


def test_fastest_never_picks_an_offer_without_transit_time():
    import uuid

    def offer(code, total, transit):
        option = candidate_from_rate(
            _portal_rate(
                rate_id=f"R-{code}",
                airline_code=code,
                total=total,
                currency="EUR",
                fx_eur_per_usd=None,
                transit_days=transit,
            ),
            200,
            Decimal("1.2"),
            now=NOW,
        )
        apply_pickup(option, None, None)
        return option_to_contract(option, uuid.uuid4(), now=NOW)

    offers = [offer("AA", "300.00", None), offer("BB", "500.00", 2), offer("CC", "400.00", 4)]
    fastest, evidence = choose_offer_with_evidence(offers, "fastest", now=NOW)
    assert fastest.carrier_code == "BB"
    assert evidence["excluded_missing_transit_candidate_ids"] == [str(offers[0].id)]
    assert choose_offer_with_evidence(offers, "cheapest", now=NOW)[0].carrier_code == "AA"
