REQUIRED_EVENTS = (
    "message_received",
    "rates_loaded",
    "offer_selected",
    "quote_recorded",
    "customer_accepted",
    "booking_confirmed",
)


def test_session_board_after_demo(client):
    run = client.post("/internal/demo/run")
    assert run.status_code == 200
    session_id = run.json()["session_id"]

    listing = client.get("/api/sessions")
    assert listing.status_code == 200
    item = next(row for row in listing.json() if row["id"] == session_id)
    assert item["stage"] == "booked"
    assert item["rfq"]["origin"] == "FRA"
    assert item["rfq"]["destination"] == "LAX"
    assert "Yellowtail" in item["carrier"]
    assert item["quote_total"] == "412.50"
    assert item["quote_currency"] == "EUR"

    detail = client.get(f"/api/sessions/{session_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["stage"] == "booked"
    assert body["simulated"] is True
    assert "Yellowtail" in body["selected_offer"]["carrier"]
    assert body["quote"]["total"] == "412.50"
    assert body["booking"]["status"] == "confirmed"
    types = [event["type"] for event in body["events"]]
    for required in REQUIRED_EVENTS:
        assert required in types

    events = client.get(f"/api/sessions/{session_id}/events")
    assert events.status_code == 200
    assert [event["type"] for event in events.json()] == types


def test_pause_and_resume_session(client):
    session_id = client.post("/internal/demo/run").json()["session_id"]
    paused = client.post(f"/api/sessions/{session_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    resumed = client.post(f"/api/sessions/{session_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False
    types = [event["type"] for event in resumed.json()["events"]]
    assert "paused" in types
    assert "resumed" in types


def test_session_missing_is_404(client):
    response = client.get("/api/sessions/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
