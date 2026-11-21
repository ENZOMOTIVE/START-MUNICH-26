"""Wordings taken from mail the live customer agents sent on 2026-11-21 (pure rules, no DB)."""

import pytest
from app.workflows.extract import extract

PICKUP_MUNICH = (
    "Could you please quote the cheapest air freight option to ORD (Chicago, US), including "
    "truck pickup from our site in 81311 (Munich South), Germany, and transport to the "
    "departure airport?\n\nThe shipment will be ready for pickup on 2026-11-28. It consists of "
    "10 pieces of spare parts for packaging machines, with a gross weight of 196 kg and a total "
    "volume of 0.72 cbm.\n\nPlease send us your all-in price in EUR, including the pickup."
)
PICKUP_FASTEST = (
    "Could you quote an all-in price in EUR for the fastest air freight option to ORD (Chicago, "
    "US)? Please arrange trucking from our site in 51447 Bergisch Gladbach, Germany, to the "
    "departure airport. The shipment is ready for pickup on 2026-11-25.\n\nIt's 12 pieces of "
    "printed marketing material, 676 kg gross and 5.79 cbm."
)
SELF_DELIVERY = (
    "We'll deliver the goods to Frankfurt airport (FRA) ourselves, so there's no pickup needed. "
    "The shipment will be ready on 2026-11-28 and consists of 6 pieces, with a gross weight of "
    "102 kg and a total volume of 0.49 cbm.\n\nCould you send me an all-in price in EUR for the "
    "cheapest air freight option from FRA to DOH for this import?"
)
SELF_DELIVERY_NO_NEED = (
    "Could you send me an all-in price in EUR for the cheapest air freight option from CGN to "
    "DFW (Dallas, US)?\n\nWe'll deliver the textile samples to Cologne airport (CGN) ourselves, "
    "so there's no need to arrange pickup. The shipment will be ready on 2026-11-27 and "
    "consists of 4 pieces, with a gross weight of 85 kg and a total volume of 0.70 cbm."
)


@pytest.mark.parametrize(
    ("subject", "body", "expected"),
    [
        (
            "Air freight quote request to ORD",
            PICKUP_MUNICH,
            {
                "origin": "MUC",
                "destination": "ORD",
                "weight_kg": "196",
                "volume_m3": "0.72",
                "pickup_zone": "81",
                "pickup_required": True,
            },
        ),
        (
            "Fastest air freight to ORD",
            PICKUP_FASTEST,
            {
                "origin": "CGN",
                "destination": "ORD",
                "weight_kg": "676",
                "volume_m3": "5.79",
                "pickup_zone": "51",
                "pickup_required": True,
                "service_intent": "fastest",
            },
        ),
        (
            "Air freight quote: FRA to DOH",
            SELF_DELIVERY,
            {"origin": "FRA", "destination": "DOH", "weight_kg": "102", "volume_m3": "0.49"},
        ),
        (
            "Air freight quote: CGN to DFW",
            SELF_DELIVERY_NO_NEED,
            {"origin": "CGN", "destination": "DFW", "weight_kg": "85", "volume_m3": "0.70"},
        ),
    ],
)
def test_live_request_wordings_are_read_completely(subject, body, expected):
    assert extract(subject, body)["fields"] == expected


def test_two_different_postcodes_are_not_guessed():
    fields = extract("Quote to ORD", "Pickup at 81311 Munich or 60504 Frankfurt, 100 kg")["fields"]
    assert fields.get("pickup_required") is True
    assert "pickup_zone" not in fields and "origin" not in fields
