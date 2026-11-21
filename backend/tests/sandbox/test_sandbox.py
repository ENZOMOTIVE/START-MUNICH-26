"""Sandbox enforcement, the simulator scenarios, and the customer replies."""

import re
import socket
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.core import netguard
from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.db import repositories
from app.db.models import JobRow
from app.sandbox import runner
from app.sandbox.scenarios import DGD_TEXT, SCENARIOS, SDS_TEXT
from app.sandbox.world import pdf_bytes
from app.workflows import documents, replies
from app.workflows.extract import assess_dg, extract, merge
from fastapi.testclient import TestClient
from sqlalchemy import text

SANDBOX = {
    "app_mode": "sandbox",
    "hackathon_api_base": "https://sandbox.invalid",
    "hackathon_gateway_base": "https://sandbox.invalid/v1/gateway",
    "hackathon_api_key": "sandbox-key",
}
PRICE = re.compile(r"\b(?:EUR|USD)\s?\d[\d,]*\.\d{2}\b")


def sandbox_settings(**over) -> Settings:
    return Settings(_env_file=None, **{**SANDBOX, **over})


@pytest.fixture
def guard():
    netguard.blocked.clear()
    netguard.install(get_settings().database_url)
    yield netguard
    netguard.uninstall()


# ---------- configuration fails closed ----------


def test_live_service_url_is_rejected_before_any_request(monkeypatch):
    opened = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, address: opened.append(address))
    with pytest.raises(ValueError, match="HACKATHON_API_BASE points at 'api.start-hack.5u.ai'"):
        sandbox_settings(hackathon_api_base="https://api.start-hack.5u.ai")
    assert opened == []


@pytest.mark.parametrize(
    "bad,message",
    [
        ({"live_actions_enabled": True}, "LIVE_ACTIONS_ENABLED must be false"),
        ({"llm_mode": "gateway"}, "LLM_MODE must be fake"),
        ({"router_backend": "gateway"}, "ROUTER_BACKEND must be keyword"),
        ({"hackathon_gateway_base": "https://api.openai.com/v1"}, "HACKATHON_GATEWAY_BASE"),
        ({"hackathon_api_key": "hk_live_0123456789"}, "not a synthetic sandbox- credential"),
        ({"gateway_openai_key": "sk-proj-abc"}, "not a synthetic sandbox- credential"),
    ],
)
def test_sandbox_refuses_unsafe_configuration(bad, message):
    with pytest.raises(ValueError, match=message):
        sandbox_settings(**bad)


def test_sandbox_with_default_urls_refuses_to_start():
    with pytest.raises(ValueError, match="sandbox refused to start"):
        Settings(_env_file=None, app_mode="sandbox")


def test_sandbox_never_reads_the_live_env_file(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "HACKATHON_API_KEY=hk_live_from_dotenv\nHACKATHON_API_BASE=https://api.start-hack.5u.ai\n"
    )
    monkeypatch.chdir(tmp_path)
    for name, value in SANDBOX.items():
        monkeypatch.setenv(name.upper(), value)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.hackathon_api_key == "sandbox-key"
        assert settings.hackathon_api_base == "https://sandbox.invalid"
    finally:
        get_settings.cache_clear()


def test_sandbox_without_a_simulator_fails_closed(monkeypatch):
    import app.platform as registry

    monkeypatch.setattr(registry, "get_settings", sandbox_settings)
    registry.set_adapters(None)
    with pytest.raises(DomainError) as raised:
        registry.get_adapters()
    assert raised.value.code == "sandbox_adapters_missing"


# ---------- outbound network is blocked ----------


def test_guard_blocks_real_hosts_and_keeps_the_database(guard, db):
    with pytest.raises(netguard.SandboxNetworkBlocked):
        socket.create_connection(("api.start-hack.5u.ai", 443), timeout=1)
    with pytest.raises(netguard.SandboxNetworkBlocked):
        socket.socket().connect(("93.184.216.34", 443))
    with pytest.raises(httpx.TransportError):
        httpx.get("https://api.openai.com/v1/models", timeout=1)
    assert len(guard.blocked) == 3
    assert db.execute(text("SELECT 1")).scalar() == 1


def test_worker_installs_the_guard_on_every_start(monkeypatch):
    from app.workers import main as worker

    class Stop(Exception):
        pass

    def stop(_seconds):
        raise Stop

    monkeypatch.setattr(worker, "get_settings", sandbox_settings)
    monkeypatch.setattr(worker, "process_jobs_once", lambda db, worker_id: [])
    monkeypatch.setattr(worker.time, "sleep", stop)
    try:
        for _ in range(2):  # a restart goes through the same code
            netguard.uninstall()
            with pytest.raises(Stop):
                worker.main()
            assert netguard._installed is not None
    finally:
        netguard.uninstall()


def test_sandbox_console_exists_only_in_sandbox_mode(client, monkeypatch):
    assert client.get("/sandbox/scenarios").status_code == 404
    assert client.post("/sandbox/run", json={"scenario": "happy_path"}).status_code == 404

    import app.main as main

    monkeypatch.setattr(main, "get_settings", sandbox_settings)
    try:
        with TestClient(main.create_app()) as sandbox:
            listed = sandbox.get("/sandbox/scenarios")
            assert {s["id"] for s in listed.json()} == set(SCENARIOS)
            result = sandbox.post("/sandbox/run", json={"scenario": "happy_path", "seed": 42})
            assert result.status_code == 200 and result.json()["passed"] is True
            assert result.json()["data"] == "SIMULATED"
            assert sandbox.post("/sandbox/run", json={"scenario": "nope"}).status_code == 404
    finally:
        netguard.uninstall()


# ---------- scenarios ----------


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario(name, db):
    result = runner.run(name, 42)
    failed = [c for c in result["checks"] if not c["passed"]]
    assert not failed, f"{name}: {failed} errors={result['errors']}"


@pytest.mark.parametrize("name", ["happy_path", "dg_missing_docs", "lost_ack", "expired_backlog"])
def test_same_scenario_and_seed_reproduce_the_same_outcome(name, db):
    first, second = runner.run(name, 42), runner.run(name, 42)
    assert first["digest"] == second["digest"]
    assert first["requests"] == second["requests"]


def test_a_clarification_is_never_the_first_quote(db):
    result = runner.run("dg_missing_docs", 42)
    thread = result["threads"]["rfq1"]
    asked = next(m for m in thread if m.get("purpose") == "dg_documents")
    quote = next(m for m in thread if m.get("purpose") == "first_quote")
    assert not PRICE.search(asked["body"]) and "Total" not in asked["body"]
    assert asked["at_s"] < quote["at_s"]
    assert len(PRICE.findall(quote["body"])) == 1
    events = result["requests"]["rfq1"]["events"]
    assert events.index("clarification_sent") < events.index("quote_recorded")
    assert events.count("quote_recorded") == 1


def test_tracking_answer_uses_only_the_recorded_update(db):
    result = runner.run("happy_path", 42)
    answer = next(m for m in result["threads"]["rfq1"] if m.get("purpose") == "tracking_answer")
    assert "Departed FRA on flight BR 204" in answer["body"]
    assert "I have no later information than that" in answer["body"]
    for invented in ("arrived", "delivered", "landed", "ETA", "on time"):
        assert invented not in answer["body"]


def test_committed_quote_keeps_its_own_evidence(db):
    from app.db.models import MessageRow, QuoteDecisionRow

    result = runner.run("happy_path", 42)
    quote_mail = next(m for m in result["threads"]["rfq1"] if m.get("purpose") == "first_quote")
    assert "EUR 585.00 all-in" in quote_mail["body"]
    decision = (
        db.query(QuoteDecisionRow)
        .filter(QuoteDecisionRow.total == 585, QuoteDecisionRow.decided_at < datetime(2026, 11, 22))
        .order_by(QuoteDecisionRow.decided_at.desc())
        .first()
    )
    evidence = decision.evidence
    assert evidence["selected_total"] == "585.00" and evidence["source_version"]
    assert evidence["rule"] == "minimum_total_among_complete_current_candidates"
    assert evidence["request"]["method"] == "rules" and evidence["charges"]
    assert db.query(MessageRow).count() > 0


# ---------- worker lease ----------


def test_a_job_left_claimed_by_a_dead_worker_is_taken_back(db):
    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    now = datetime.now(UTC)
    dead = JobRow(
        kind="noop",
        payload={},
        status="claimed",
        claimed_by="gone",
        claimed_at=now - timedelta(minutes=5),
        created_at=long_ago,
        run_after=long_ago,
    )
    db.add(dead)
    db.flush()
    taken = repositories.claim_job(db, "worker-2")
    assert (taken.id, taken.claimed_by, taken.attempts) == (dead.id, "worker-2", 1)
    again = repositories.claim_job(db, "worker-3")  # a live claim is not stolen
    assert again is None or again.id != dead.id
    dead.status = "done"
    if again is not None:
        again.status = "pending"


# ---------- reading the customer's mail ----------


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            "Please quote 300 kg, 1.2 cbm from FRA to ORD.",
            {"origin": "FRA", "destination": "ORD", "weight_kg": "300", "volume_m3": "1.2"},
        ),
        (
            "RFQ: 1,200 kg MUC-LAX, urgent",
            {
                "origin": "MUC",
                "destination": "LAX",
                "weight_kg": "1200",
                "service_intent": "fastest",
            },
        ),
        (
            "2 tonnes to SIN from Hamburg (HAM), pickup zone 20",
            {
                "origin": "HAM",
                "destination": "SIN",
                "weight_kg": "2000",
                "pickup_zone": "20",
                "pickup_required": True,
            },
        ),
        ("Quote please for our RFQ to the USA", {}),
    ],
)
def test_fields_are_read_from_plain_text(body, expected):
    assert extract("Request", body)["fields"] == expected


def test_unusable_weights_are_asked_about_not_guessed():
    ambiguous = merge([("RFQ", "1.200 kg from FRA to ORD")])
    assert ambiguous["missing"] == ["weight_kg"] and "ambiguous" in ambiguous["notes"][0]
    pounds = merge([("RFQ", "500 lbs from FRA to ORD")])
    assert pounds["missing"] == ["weight_kg"] and "not in kg" in pounds["notes"][0]


def test_dangerous_goods_follow_the_evidence():
    doc = documents.evidence("a.pdf", pdf_bytes(DGD_TEXT))
    sheet = documents.evidence("b.pdf", pdf_bytes(SDS_TEXT))
    named_only = {"filename": "DGD.pdf", "size": 10}  # mailbox metadata: a name, no content
    by_name = assess_dg([("lithium batteries UN3480", [named_only])])
    assert by_name["docs_missing"] == ["dgd", "msds"] and by_name["blocked"]
    assert assess_dg([("alkaline AA batteries, non-dangerous toys", [])])["status"] == "none"
    assert assess_dg([("lithium-ion batteries UN3480", [])])["status"] == "docs_missing"
    assert assess_dg([("non-dangerous power banks UN3480", [])])["status"] == "conflict"
    assert assess_dg([("lithium batteries UN3480", [doc, sheet])])["status"] == "documented"
    unreadable = assess_dg([("UN3480", [doc, documents.evidence("b.pdf", b"")])])
    assert unreadable["status"] == "docs_missing" and unreadable["docs_missing"] == ["msds"]
    assert unreadable["unreadable_attachments"] == ["b.pdf"]
    cleared = assess_dg([("non-dangerous chairs UN3480", []), ("it is not dangerous goods", [])])
    assert cleared["status"] == "resolved_non_dg"


# ---------- customer replies: facts and wording ----------

WHEN = datetime(2026, 11, 21, 9, 15, tzinfo=UTC)


def test_final_quote_snapshot_and_single_total():
    mail = replies.final_quote(
        "Anna",
        "Quote request FRA-ORD",
        "FRA",
        "ORD",
        "300",
        "Bravo Freight",
        "585.00",
        "EUR",
        5,
        ["air_all_in"],
        WHEN,
    )
    assert mail["subject"] == "Re: Quote request FRA-ORD"
    assert mail["body"] == (
        "Hello Anna,\n\n"
        "Thank you for your request. Here is our offer for 300 kg from FRA to ORD:\n\n"
        "Total: EUR 585.00 all-in\n"
        "Carrier: Bravo Freight, transit 5 days\n"
        "Included: air all in\n"
        "Valid until: 21 Nov 2026 09:15 UTC\n\n"
        "To book, reply to this email and confirm that you accept.\n\n"
        "Best regards,\nFiveU Freight Desk"
    )
    assert PRICE.findall(mail["body"]) == ["EUR 585.00"]


def test_quote_without_transit_says_so_instead_of_inventing_one():
    mail = replies.final_quote(
        None, "RFQ", "FRA", "ORD", "300", "X Air", "1.00", "EUR", None, [], None
    )
    assert "transit time not confirmed" in mail["body"] and "days" not in mail["body"]
    assert mail["body"].startswith("Hello,\n")


def test_questions_carry_no_price_and_ask_only_what_is_missing():
    missing = replies.missing_info("Anna", "RFQ", "FRA", "ORD", ["weight_kg"], [])
    assert "the gross weight in kg" in missing["body"]
    assert "airport" not in missing["body"]
    docs = replies.dg_documents("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd", "msds"])
    conflict = replies.dg_conflict("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd", "msds"])
    for mail in (missing, docs, conflict):
        assert not PRICE.search(mail["body"]) and "Total" not in mail["body"]
        assert mail["purpose"] != "first_quote"
    assert "UN3480" in docs["body"] and "non-dangerous" in conflict["body"]


def test_booking_and_tracking_never_overstate():
    pending = replies.booking_submitted("Anna", "RFQ", "Bravo Freight", "TM-1")
    assert "not confirmed yet" in pending["body"] and "is confirmed" not in pending["body"]
    done = replies.booking_confirmed(
        "Anna", "RFQ", "Bravo Freight", "BK-AB12CD34", "FRA", "ORD", "300", "585.00", "EUR"
    )
    assert "BK-AB12CD34" in done["body"] and "300 kg from FRA to ORD" in done["body"]
    waiting = replies.tracking_answer("Anna", "RFQ", "BK-1", "submitted", "Bravo Freight", None)
    assert "not confirmed yet" in waiting["body"] and "BK-1" not in waiting["body"]
    silent = replies.tracking_answer("Anna", "RFQ", "BK-1", "confirmed", "Bravo Freight", None)
    assert "cannot tell you where the shipment is" in silent["body"]
    known = replies.tracking_answer(
        "Anna",
        "RFQ",
        "BK-1",
        "confirmed",
        "Bravo Freight",
        {"status": "Departed FRA", "reported_at": WHEN},
    )
    assert "Departed FRA (reported 21 Nov 2026 09:15 UTC)" in known["body"]
