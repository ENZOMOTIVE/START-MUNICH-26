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


def test_the_body_outranks_a_forwarded_subject_line():
    body = (
        "Could you please quote your cheapest air freight option to EZE (Buenos Aires, AR), "
        "including truck pickup from our site in 73825 Esslingen, Germany, and transport to "
        "the departure airport?\n\nThe shipment consists of 7 pieces of furniture samples, with "
        "a gross weight of 1210 kg and a total volume of 8.47 cbm."
    )
    fields = extract("RE: FW: VIENNA AIRPORT - RUH / 30 PCS 708 KGS", body)["fields"]
    assert (fields["origin"], fields["destination"], fields["weight_kg"]) == ("STR", "EZE", "1210")
    # A subject still supplies what the body leaves out.
    assert extract("Quote FRA to DEL", "736 kg gross, 6.88 cbm")["fields"]["destination"] == "DEL"


def test_truckers_are_asked_by_full_postcode():
    # Live truckers refused "pickup zone 80" as "pickup postcode 80": they price by postcode.
    from app.workflows.extract import pickup_postcode
    from app.workflows.replies import pickup_enquiry

    assert pickup_postcode(PICKUP_MUNICH, "81") == "81311"
    assert pickup_postcode(PICKUP_MUNICH, "60") is None
    mail = pickup_enquiry("81", "MUC", "196", "0.72", "0a1b2c3d-4e5f", "81311")
    assert mail["subject"] == "Pickup enquiry: 81311 to MUC [ref 0a1b2c3d-4e5f]"
    assert "From: postcode 81311" in mail["body"] and "zone" not in mail["body"]


def test_two_different_postcodes_are_not_guessed():
    fields = extract("Quote to ORD", "Pickup at 81311 Munich or 60504 Frankfurt, 100 kg")["fields"]
    assert fields.get("pickup_required") is True
    assert "pickup_zone" not in fields and "origin" not in fields


def test_lots_are_added_up_to_one_consignment():
    """Round 62 was quoted on lot 1 alone and refused as too low."""
    mixed = extract(
        "",
        "Fastest option to SCL, pickup at our site in 53521 (Bonn).\n\n"
        "Lot 1: 1 carton, together 465 kg gross (404 kg net), together 3.87 cbm\n"
        "Lot 2: 3 pallets, each 160 kg gross (142 kg net), each 100 x 60 x 46 cm\n"
        "Lot 3: 3 drums, each 59 kg gross (55 kg net), each 110 x 110 x 128 cm\n",
    )["fields"]
    assert (mixed["weight_kg"], mixed["volume_m3"]) == ("1122", "9.344")
    plain = extract(
        "",
        "Cheapest option from MUC to CAI.\n"
        "Lot 1: 9 pieces, 345 kg gross, 0.58 cbm\n"
        "Lot 2: 1 piece, gross weight 141 kg, volume 2.04 cbm",
    )["fields"]
    assert (plain["weight_kg"], plain["volume_m3"]) == ("486", "2.620")


def test_a_un_number_in_a_packing_list_asks_for_the_dangerous_goods_papers():
    """Round 61: the mail said "general cargo, non-dangerous", the packing list UN1760."""
    from app.workflows.extract import assess_dg

    packing_list = {"filename": "PL.xlsx", "readable": True, "kinds": [], "un_numbers": ["UN1760"]}
    dg = assess_dg([("4 pieces of cleaning agent. General cargo, non-dangerous.", [packing_list])])
    assert dg["blocked"] and dg["signals"] == ["UN1760"] and dg["docs_missing"]
