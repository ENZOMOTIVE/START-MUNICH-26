"""Operator-facing behaviour: token, event stream, usage, review list, send spacing."""

import hashlib
import re
import uuid
from pathlib import Path

from app.sandbox import runner
from app.workflows import replies
from app.workflows.followup import known_tariff_checksums

KIT = Path(__file__).resolve().parents[3] / "starter-kit"
PRICE = re.compile(r"\b(?:EUR|USD)\s?\d[\d,]*\.\d{2}\b")


class _Settings:
    """Stands in for get_settings() where one value has to differ."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def live_allowed(self) -> bool:
        return self.__dict__.get("live", False)


def test_pause_needs_the_operator_token_when_one_is_set(client, monkeypatch):
    import app.api.sessions as api

    session_id = client.post("/internal/demo/run").json()["session_id"]
    monkeypatch.setattr(api, "get_settings", lambda: _Settings(operator_token="s3cret"))
    assert client.post(f"/api/sessions/{session_id}/pause").status_code == 401
    wrong = {"X-Operator-Token": "nope"}
    assert client.post(f"/api/sessions/{session_id}/pause", headers=wrong).status_code == 401
    right = {"X-Operator-Token": "s3cret"}
    assert client.post(f"/api/sessions/{session_id}/pause", headers=right).json()["paused"] is True
    assert client.post(f"/api/sessions/{session_id}/resume", headers=right).status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 200  # reading stays open


def test_event_stream_resumes_without_repeating(client):
    session_id = client.post("/internal/demo/run").json()["session_id"]
    total = len(client.get(f"/api/sessions/{session_id}/events").json())

    def ids(response):
        return [int(i) for i in re.findall(r"^id: (\d+)$", response.text, re.M)]

    full = client.get(f"/api/sessions/{session_id}/stream?follow=false")
    assert full.headers["content-type"].startswith("text/event-stream")
    assert ids(full) == list(range(1, total + 1))
    assert "event: message_received" in full.text
    resumed = client.get(
        f"/api/sessions/{session_id}/stream?follow=false", headers={"Last-Event-ID": "3"}
    )
    assert ids(resumed) == list(range(4, total + 1))
    assert ids(client.get(f"/api/sessions/{session_id}/stream?follow=false&after={total}")) == []
    missing = client.get(f"/api/sessions/{uuid.uuid4()}/stream?follow=false")
    assert missing.status_code == 404


def test_usage_reports_cost_as_unknown_until_it_is_known(client, db):
    runner.run("model_extraction", 42)
    rows = client.get("/api/usage").json()
    fake = next(r for r in rows if r["model"] == "sandbox-fake-model")
    assert fake["simulated"] is True and fake["calls"] >= 1
    assert (fake["cost_kind"], fake["observed_cost"], fake["estimated_cost"]) == (
        "unknown",
        None,
        None,
    )


def test_review_list_shows_what_waits_for_a_person(client, db):
    runner.run("tariff_update", 42)
    runner.run("expired_backlog", 42)
    review = client.get("/api/review").json()
    files = [f for m in review["mail"] if m["tariff"] for f in m["tariff"]["files"]]
    new = next(f for f in files if f["filename"] == "aurelia-air-cargo-tariff-2026-12.xlsx")
    assert new["status"] == "review_required" and len(new["sha256"]) == 64
    assert any(m["unmatched"] and m["kind"] == "tracking" for m in review["mail"])
    assert any(s["reason"] == "expired" for s in review["sessions"])


def test_an_approved_tariff_file_is_recognised():
    approved = (KIT / "yellowtail-air-freight-tariff-2026-11.txt").read_bytes()
    assert hashlib.sha256(approved).hexdigest() in known_tariff_checksums()
    assert hashlib.sha256(approved + b" ").hexdigest() not in known_tariff_checksums()


def test_live_sends_are_spaced_under_the_mailbox_limit(db, monkeypatch):
    import app.platform.outbox as outbox

    live = _Settings(live=True, send_min_interval_ms=1100)
    monkeypatch.setattr(outbox, "get_settings", lambda: live)
    monkeypatch.setattr(outbox, "LAST_SEND", f"last_send_test_{uuid.uuid4().hex}")
    result = runner.run("booking_retry", 42)
    assert result["passed"], [c for c in result["checks"] if not c["passed"]]
    times = [m["at_s"] for m in result["threads"]["rfq1"] if m["direction"] == "out"]
    assert len(times) == 3 and all(b - a >= 1 for a, b in zip(times, times[1:], strict=False))


def test_a_run_can_be_stopped_part_way(db):
    early = runner.run("happy_path", 42, until_s=5)
    assert early["simulated_seconds"] <= 10
    assert early["requests"]["rfq1"]["stage"] == "quoted"
    assert early["requests"]["rfq1"]["booking"] is None


def test_carrier_mails_state_only_the_request():
    enquiry = replies.pickup_enquiry("60", "FRA", "300", "1.2", "0a1b2c3d-4e5f")
    assert enquiry["subject"] == "Pickup enquiry: zone 60 to FRA [ref 0a1b2c3d-4e5f]"
    assert "300 kg, 1.2 cbm" in enquiry["body"] and not PRICE.search(enquiry["body"])
    request = replies.carrier_booking_request("FRA", "DFW", "300", "621.00", "EUR", "TM-1")
    assert "Agreed price: EUR 621.00" in request["body"] and "TM-1" in request["subject"]
    failed = replies.booking_failed("Anna", "RFQ", "Bravo Freight", "the rate expired")
    assert "Nothing has been booked" in failed["body"] and "confirmed" not in failed["body"]
