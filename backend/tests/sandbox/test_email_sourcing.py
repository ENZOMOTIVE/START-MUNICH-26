"""Rates that arrive by mail: what a reply must say, who may send it, what a person decides."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.db import repositories
from app.db.models import MessageRow
from app.pricing import tariff_candidates
from app.pricing.parsers.base import LaneRate
from app.sandbox import runner
from app.sandbox.scenarios import (
    DFW_BODY,
    DFW_CUSTOMER,
    HIMALAYA,
    KTM_BODY,
    KTM_CUSTOMER,
    ZEPHYR,
    rfq,
    scenario,
)
from app.workflows import followup
from app.workflows.followup import read_rate_bid
from sqlalchemy import select

REQUEST = SimpleNamespace(origin="FRA", destination="KTM", weight_kg=Decimal("300"))
NOW = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
GOOD = "FRA to KTM, 300 kg: EUR 1,240.00 all-in, transit 4 days, valid until 2026-11-23."
ELBFLUG = "elbflug-air-freight@hackloop.mail"
YELLOWTAIL = "yellowtail-air-freight@hackloop.mail"


def ktm(**more):
    return [rfq(**{"body": KTM_BODY, "subject": "Quote FRA-KTM", "customer": KTM_CUSTOMER, **more})]


def _mails(db, world) -> list[MessageRow]:
    ids = [str(m["id"]) for m in world.inbox]
    return list(db.scalars(select(MessageRow).where(MessageRow.external_id.in_(ids))))


def _bids(db, world) -> list[dict]:
    root = str(world.requests["rfq1"]["root"])
    message = next(m for m in _mails(db, world) if m.external_id == root)
    session = repositories.session_by_thread(db, message.thread_id)
    return [e.payload for e in repositories.events_for(db, session.id) if e.type == "carrier_bid"]


def test_a_complete_reply_is_accepted_as_it_stands():
    assert read_rate_bid(GOOD, REQUEST, NOW, direct_reply=False) == {
        "status": "accepted",
        "reasons": [],
        "amount": "1240.00",
        "currency": "EUR",
        "valid_until": "2026-11-23T23:59:00+00:00",
        "transit_days": 4,
    }
    # The quoted enquiry in a direct reply already names the route.
    direct = read_rate_bid("EUR 900.00 all-in.", REQUEST, NOW, direct_reply=True)
    assert direct["status"] == "accepted"


@pytest.mark.parametrize(
    ("text", "status", "reason"),
    [
        ("FRA to ULN, 300 kg: EUR 500.00 all-in.", "rejected", "another route"),
        (GOOD.replace("2026-11-23", "2026-11-20"), "rejected", "validity has passed"),
        ("FRA to KTM, 250 kg: EUR 900.00 all-in.", "review", "weight"),
        ("FRA to KTM, 300 kg: USD 900.00 all-in.", "review", "USD"),
        ("FRA to KTM: EUR 3.10 per kg plus fuel.", "review", "on top"),
        ("FRA to KTM, 300 kg: EUR 900.00.", "review", "all charges"),
        ("FRA to KTM: EUR 900.00 all-in, or EUR 950.00 all-in express.", "review", "one price"),
        ("EUR 900.00 all-in.", "review", "restate the route"),
    ],
)
def test_a_reply_that_is_wrong_or_leaves_something_open_is_not_used(text, status, reason):
    bid = read_rate_bid(text, REQUEST, NOW, direct_reply=False)
    assert bid["status"] == status and any(reason in r for r in bid["reasons"]), bid


def test_each_unusable_reply_is_recorded_with_its_reason_and_nothing_is_quoted(db, client):
    seen = []
    case = {
        **runner.SCENARIOS["email_rate_unusable_replies"],
        "actions": [{"at": 200, "do": lambda db, world: seen.extend(_bids(db, world))}],
    }
    result = runner.run(case, 42)
    assert all(c["passed"] for c in result["checks"])
    verdict = {b["carrier"]: (b["status"], " ".join(b["reasons"])) for b in seen}
    assert verdict["Zephyr Steppe Cargo"][0] == "rejected"
    assert "another route" in verdict["Zephyr Steppe Cargo"][1]
    assert verdict["Pampero Air Cargo"][0] == "rejected"
    assert "validity has passed" in verdict["Pampero Air Cargo"][1]
    assert verdict["Himalaya Bridge Air"][0] == "review"
    assert verdict["Rate desk"] == ("review", "it names no listed airline that could be booked")

    # The two that need a person are on the review list, and resolving one takes it off.
    db.commit()
    waiting = [m for m in client.get("/api/review").json()["mail"] if m["bid"]]
    himalaya = next(m for m in reversed(waiting) if m["sender"] == HIMALAYA)
    assert himalaya["bid"]["status"] == "review_required" and himalaya["bid"]["reasons"]
    done = client.post(f"/api/review/mail/{himalaya['external_id']}/resolve").json()
    assert done == {"external_id": himalaya["external_id"], "resolved": True}
    left = [m["external_id"] for m in client.get("/api/review").json()["mail"]]
    assert himalaya["external_id"] not in left


def test_a_reply_from_someone_we_did_not_ask_is_refused(db):
    seen = []
    intruder = "lagoon-isles-airways@hackloop.mail"
    case = scenario(
        "unasked_sender",
        ktm(),
        {},
        airlines={ZEPHYR: {"bid": {"price": "400.00", "from": intruder}}},
        actions=[{"at": 200, "do": lambda db, world: seen.extend(_bids(db, world))}],
    )
    request = runner.run(case, 42)["requests"]["rfq1"]
    assert [(b["carrier"], b["status"]) for b in seen] == [("Lagoon Isles Airways", "rejected")]
    assert "did not ask" in seen[0]["reasons"][0]
    assert request["quote"] is None and request["outbound"] == ["no_offer"]


def test_an_operator_can_approve_a_bid_that_waited_for_review_and_pricing_resumes(db):
    states = []

    def approve(db, world):
        for mail in _mails(db, world):
            if (mail.raw.get("bid") or {}).get("status") == "review_required":
                states.append(followup.apply_review(db, mail, approve=True))
                states.append(mail.raw["bid"]["status"])

    vague = "{origin} to {destination}, {weight} kg: EUR 1,300.00, valid until {valid}."
    case = scenario(
        "bid_review",
        ktm(),
        {},
        airlines={HIMALAYA: {"bid": {"body": vague}, "delay": 30}},
        actions=[{"at": 60, "do": approve}],
    )
    request = runner.run(case, 42)["requests"]["rfq1"]
    assert states == [{"bid_used": True}, "approved"]
    assert request["quote"]["carrier"] == "Himalaya Bridge Air"
    assert request["quote"]["total"] == "1300.00" and request["quote"]["source"] == "mail"
    assert request["stage"] == "booked"  # booked by mail with the airline that sent the bid
    assert request["quote_latency_s"] >= 60  # nothing was quoted before the approval


def test_a_bid_is_combined_with_the_trucker_price_when_the_request_needs_a_pickup(db):
    case = scenario(
        "bid_with_pickup",
        ktm(body="Please quote 300 kg, 1.2 cbm from FRA to KTM with pickup in zone 60."),
        {},
        airlines={HIMALAYA: {"bid": {"price": "1240.00"}, "delay": 30}},
        truckers={"taunusfracht@hackloop.mail": {"price": "180.00"}},
    )
    request = runner.run(case, 42)["requests"]["rfq1"]
    # Air freight by mail 1240.00 + pickup by mail 180.00, nothing else.
    assert request["quote"]["total"] == "1420.00" and request["stage"] == "booked"


def test_approving_a_revised_tariff_reprices_open_requests_from_the_approved_file(db, monkeypatch):
    def tariff(key, sender):
        files = [{"id": 1, "filename": f"{key}-2026-12.xlsx", "size": 2048, "content": key}]
        body = "Please find our new tariff attached."
        return {"key": key, "from": sender, "subject": "Tariff", "body": body, "attachments": files}

    # The approved December file: Elbflug's FRA-DFW rate from 300 kg rises from 1.19 to 1.25.
    data = json.loads(Path(tariff_candidates.config.SNAPSHOT).read_text())
    records = [record for tariff_set in data["sets"] for record in tariff_set["records"]]
    for record in records:
        lane = (record["carrier"], record["origin"], record["destination"])
        if lane == ("Elbflug Air Freight", "FRA", "DFW"):
            record["breaks"]["300"] = "1.25"
    revised = tariff_candidates.TariffBook([LaneRate.from_dict(r) for r in records])
    applied = []

    def approve(db, world):
        book = classmethod(lambda cls, path=None: revised)
        monkeypatch.setattr(tariff_candidates.TariffBook, "from_snapshot", book)
        held = next(m for m in _mails(db, world) if m.sender == ELBFLUG and "tariff" in m.raw)
        applied.append(followup.apply_review(db, held))

    case = scenario(
        "tariff_refresh",
        [
            tariff("elbflug", ELBFLUG),
            tariff("yellowtail", YELLOWTAIL),
            rfq(body=DFW_BODY, subject="Quote FRA-DFW", customer=DFW_CUSTOMER, at=10),
        ],
        {},
        airlines={ELBFLUG: {"delay": 30}, YELLOWTAIL: {}},
        actions=[{"at": 90, "do": approve}],
    )
    result = runner.run(case, 42)
    monkeypatch.undo()
    followup.known_tariff_checksums.cache_clear()
    request = result["requests"]["rfq1"]
    assert applied == [{"repriced_requests": 1}]
    # Worked by hand: 300 x 1.25 = 375.00, fuel 300 x 0.74 = 222.00, security 300 x 0.14 =
    # 42.00, total 639.00. The old sheet gave 621.00; Yellowtail's stays on hold.
    assert request["quote"]["carrier"] == "Elbflug Air Freight"
    assert request["quote"]["total"] == "639.00"
    assert request["quote_latency_s"] >= 80  # both carriers were on hold until the approval
    assert request["stage"] == "booked"


def test_listed_carriers_and_the_rate_desk_are_not_stray_recipients(db):
    result = runner.run("email_rate_rate_desk", 42)
    assert all(c["passed"] for c in result["checks"])
    assert result["totals"]["stray_outbound"] == 0
    assert result["totals"]["carrier_mails_sent"] == 5  # three airlines, rate desk, booking
