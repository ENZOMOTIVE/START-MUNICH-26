from decimal import Decimal

import pytest

from pricing.money import (D, MoneyError, chargeable_weight, fx_to_eur, parse_number, q2, to_kg, to_m3,
                           volume_from_dims)


@pytest.mark.parametrize("raw,expected", [
    ("EUR 75.00", "75.00"), ("75,00", "75.00"), ("1,234.50", "1234.50"), ("1.234,50", "1234.50"),
    ("3.73", "3.73"), ("  0,96 ", "0.96"), ("80", "80"), ("EUR 1.29", "1.29"),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "1,2,3", "12a", "a312", "57;", "3)24", "L13"])
def test_parse_number_refuses_garbage(raw):
    with pytest.raises(MoneyError):
        parse_number(raw)


def test_float_refused():
    with pytest.raises(MoneyError):
        D(3.73)


def test_unit_conversion_weight():
    assert to_kg("100", "lb") == Decimal("45.359237")         # exact international pound
    assert to_kg("1.5", "t") == Decimal("1500.0")
    assert to_kg("2500", "g") == Decimal("2.5")
    with pytest.raises(MoneyError):
        to_kg("1", "stone")


def test_unit_conversion_volume():
    assert to_m3("1", "cbm") == Decimal(1)
    assert to_m3("250000", "cm3") == Decimal("0.25")
    assert to_m3("500", "l") == Decimal("0.5")
    assert to_m3("10", "ft3") == Decimal("0.28316846592")
    # 2 pieces 60x50x40 cm = 2 * 0.12 m3
    assert volume_from_dims("60", "50", "40", "cm", 2) == Decimal("0.24")
    assert volume_from_dims("1200", "800", "1000", "mm") == Decimal("0.96")


def test_chargeable_weight():
    assert chargeable_weight("50", "0.6") == Decimal("100.2")     # 0.6 * 167
    assert chargeable_weight("500", "1.0") == Decimal("500")
    assert chargeable_weight("50", "0.6", divisor=200) == Decimal("120.0")


def test_rounding_half_up():
    assert q2(Decimal("372.115")) == Decimal("372.12")
    assert q2(Decimal("372.114999")) == Decimal("372.11")
    assert q2(Decimal("0.005")) == Decimal("0.01")


def test_fx():
    # portal FX = EUR per 1 unit of the returned currency
    assert fx_to_eur(Decimal("1000.00"), "USD", Decimal("0.9234")) == Decimal("923.400000")
    assert fx_to_eur(Decimal("10"), "EUR", None) == Decimal("10")
    with pytest.raises(MoneyError):
        fx_to_eur(Decimal("10"), "USD", None)
    with pytest.raises(MoneyError):
        fx_to_eur(Decimal("10"), "USD", Decimal("0"))
