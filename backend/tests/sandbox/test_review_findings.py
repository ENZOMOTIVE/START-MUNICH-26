"""Regression tests for the defects found in the review of commit 3fdda69."""

import asyncio
import re
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from app.db.models import JobRow
from app.db.session import get_session_factory
from app.platform import jobs
from app.platform.mailbox import poll_mailbox_once
from app.pricing.fixtures import candidates_from_fixtures
from app.pricing.portal_client import PortalClient, PortalError, PortalState
from app.sandbox import runner
from app.sandbox.scenarios import PORTAL, rfq, scenario
from app.workflows import replies
from app.workflows.booking import acceptance_problem

ROOT = Path(__file__).resolve().parents[3]
QUOTE = (Decimal("500.00"), "EUR", "FRA", "ORD")
# 100 kg x 5.00 = EUR 500.00, the only carrier on this portal.
PORTAL_500 = {
    **PORTAL,
    "lanes": {
        "FRA-ORD": [
            {"code": "FI", "name": "Five Hundred Air", "min": "50.00", "transit": 2,
             "per_kg": {0: "5.00"}}
        ]
    },
}


# ---------- acceptance must match the quote exactly ----------


@pytest.mark.parametrize(
    "reply,problem",
    [
        ("We accept if the surcharge is removed.", "conditional_or_deferred"),
        ("Please book only after I confirm tomorrow.", "conditional_or_deferred"),
        ("We accept your quote of EUR 100.", "amount_differs_from_quote"),
        (
            "We accept your EUR 500 quote, but change the destination to SFO.",
            "route_differs_from_quote",
        ),
        ("We accept your quote of USD 500.", "amount_differs_from_quote"),
        ("We accept your EUR 500 quote.", None),
        ("We accept the quote. Please go ahead and book.", None),
    ],
)
def test_only_an_acceptance_of_the_quote_as_sent_passes(reply, problem):
    assert acceptance_problem(reply, *QUOTE) == problem


@pytest.mark.parametrize(
    "reply",
    [
        "We accept if the surcharge is removed.",
        "Please book only after I confirm tomorrow.",
        "We accept your quote of EUR 100.",
        "We accept your EUR 500 quote, but change the destination to SFO.",
    ],
)
def test_a_changed_or_conditional_reply_never_books(reply, db):
    customer = {"budget": "600.00", "truth": ["FRA", "ORD", "100 kg"], "accept_text": reply}
    case = scenario(
        "acceptance_guard",
        [rfq(body="Please quote 100 kg from FRA to ORD.", customer=customer)],
        {},
        portal=PORTAL_500,
    )
    result = runner.run(case, 42)
    request = result["requests"]["rfq1"]
    assert request["quote"]["total"] == "500.00"
    assert request["booking"] is None and request["stage"] == "quoted"
    assert result["totals"]["booking_requests"] == 0
    assert "booking_confirmed" not in request["outbound"]
    assert "customer_accepted" not in request["events"]


def test_an_unconditional_acceptance_of_the_same_quote_books(db):
    customer = {
        "budget": "600.00",
        "truth": ["FRA", "ORD", "100 kg"],
        "accept_text": "We accept your quote of EUR 500.00. Please book it.",
    }
    case = scenario(
        "acceptance_positive",
        [rfq(body="Please quote 100 kg from FRA to ORD.", customer=customer)],
        {},
        portal=PORTAL_500,
    )
    request = runner.run(case, 42)["requests"]["rfq1"]
    assert (request["stage"], request["booking"]["status"]) == ("booked", "confirmed")
    assert request["quote"]["total"] == "500.00"


# ---------- pricing ----------


def test_a_fixed_fixture_price_is_not_offered_for_another_weight():
    def offers(kg):
        request = {"id": str(uuid.uuid4()), "origin": "FRA", "destination": "LAX",
                   "weight_kg": kg, "volume_m3": None, "pickup_required": True,
                   "pickup_zone": "60", "service_intent": "cheapest"}
        return candidates_from_fixtures(request, fixtures_dir=ROOT / "fixtures")

    assert all(c.complete for c in offers("250")) and len(offers("250")) == 3
    assert not any(c.complete for c in offers("800"))


def test_portal_lanes_and_breaks_are_fetched_once_for_many_requests(db):
    before = runner.run("paging_250", 42, share_portal_cache=False)["totals"]["portal_requests"]
    after = runner.run("paging_250", 42)
    assert after["passed"]
    # Two requests on the same lane: 1 lanes call + 3 breaks calls are saved the second time.
    assert before - after["totals"]["portal_requests"] == 4
    assert after["totals"]["portal_cache_hits"] >= 4


def test_no_request_is_sent_while_the_portal_budget_is_exhausted():
    sent = []

    def handler(request):
        sent.append(request.url.path)
        return httpx.Response(200, json={"lanes": []})

    async def attempt():
        http = httpx.AsyncClient(base_url="https://sandbox.invalid",
                                 transport=httpx.MockTransport(handler))
        state = PortalState()
        state.rate_remaining = 0
        state.window_reset = time.monotonic() + 600  # the portal said: wait ten minutes
        async with PortalClient(http=http, state=state) as client:
            with pytest.raises(PortalError) as raised:
                await client.rate("FRA", "ORD", "AL", 300, 0)
            return raised.value.kind

    assert asyncio.run(attempt()) == "rate_limited"
    assert sent == []


# ---------- intake and retries ----------


def test_only_one_poller_polls_at_a_time(db):
    class Mailbox:
        polls = 0

        def poll(self, since_id, limit=100):
            self.polls += 1
            return {"messages": [], "next_since_id": since_id}

    first, second = Mailbox(), Mailbox()
    poll_mailbox_once(db, first)  # this transaction now holds the poller lock
    with get_session_factory()() as other:
        assert poll_mailbox_once(other, second) == 0
    assert (first.polls, second.polls) == (1, 0)


def test_a_failed_job_is_not_retried_in_the_same_cycle(db, monkeypatch):
    from app.core.errors import DomainError

    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    retry = JobRow(kind="route_message", payload={"message_id": str(uuid.uuid4())},
                   created_at=long_ago, run_after=long_ago)
    db.add(retry)
    db.flush()

    def temporarily_failing(_db, _message_id):
        raise DomainError("send_failed")

    monkeypatch.setattr(jobs, "route_message", temporarily_failing)
    processed = jobs.process_jobs_once(db, "worker-1", limit=5)
    ours = [j for j in processed if j.id == retry.id]
    assert len(ours) == 1 and (retry.status, retry.attempts) == ("pending", 1)
    assert retry.run_after > retry.claimed_at  # backed off: not claimable again right now

    def permanent(_db, _message_id):
        raise DomainError("invalid_weight")

    retry.run_after = long_ago
    monkeypatch.setattr(jobs, "route_message", permanent)
    jobs.process_jobs_once(db, "worker-1", limit=5)
    assert retry.status == "failed" and retry.attempts == 2  # bad input is not retried


def test_mail_no_agent_handles_is_left_for_a_person(db):
    case = scenario(
        "unsupported_mail",
        [{"key": "desk", "from": "ratedesk@hackloop.mail", "subject": "Rate desk answer",
          "body": "Here is the answer to your question."}],
        {},
    )
    result = runner.run(case, 42)
    assert result["totals"]["mails_stored"] == 1
    from app.db.models import MessageRow

    newest = db.query(MessageRow).filter(MessageRow.sender == "ratedesk@hackloop.mail")
    flagged = newest.order_by(MessageRow.created_at.desc()).first()
    assert flagged.classification == "rate_desk_reply"
    assert flagged.raw["unmatched"]["reason"] == "no agent handles this kind of mail"


# ---------- operator takeover ----------


def test_operator_edits_and_sends_a_draft_through_the_outbox(client, db):
    session_id = client.post("/internal/demo/run").json()["session_id"]
    actions = client.get(f"/api/sessions/{session_id}/actions").json()
    assert {a["delivery_status"] for a in actions} <= {"confirmed", "pending", "sending"}
    sent = next(a for a in actions if a["kind"] == "first_quote")
    url = f"/api/sessions/{session_id}/actions/{sent['id']}"

    # Not taken over yet: nothing may be changed or sent by hand.
    assert client.put(url, json={"body": "x"}).status_code == 409
    client.post(f"/api/sessions/{session_id}/pause")
    # Already delivered: it is not a draft any more, and it cannot be sent twice.
    assert client.put(url, json={"body": "x"}).json()["error"]["code"] == "not_a_draft"
    again = client.post(f"{url}/send").json()
    assert again["delivery_status"] == "confirmed"
    assert client.post(f"{url}/resolve", json={"sent": True}).json()["error"]["code"] == (
        "not_unknown"
    )
    client.post(f"/api/sessions/{session_id}/resume")


def test_operator_resolves_an_unknown_send(client, db):
    from app.db.models import ActionIntentRow

    session_id = client.post("/internal/demo/run").json()["session_id"]
    quote = next(
        a for a in client.get(f"/api/sessions/{session_id}/actions").json()
        if a["kind"] == "first_quote"
    )
    intent = db.get(ActionIntentRow, uuid.UUID(quote["id"]))
    original = intent.delivery_status
    intent.delivery_status = "UNKNOWN"
    db.flush()
    client.post(f"/api/sessions/{session_id}/pause")
    url = f"/api/sessions/{session_id}/actions/{quote['id']}"
    assert client.post(f"{url}/send").json()["error"]["code"] == "unknown_no_blind_resend"
    resolved = client.post(f"{url}/resolve", json={"sent": True}).json()
    assert resolved["delivery_status"] == "confirmed"
    client.post(f"/api/sessions/{session_id}/resume")
    intent.delivery_status = original


# ---------- customer replies follow the voice guide ----------


def test_every_customer_mail_follows_the_voice_guide():
    guide = (ROOT / "docs" / "agent-human-voice.md").read_text()
    avoid = guide.split("## Avoid")[1].split("## Formatting")[0]
    banned = [phrase.lower() for phrase in re.findall(r'"([^"]+)"', avoid)]
    assert "rest assured" in banned and "kindly note" in banned
    when = datetime(2026, 11, 21, 9, 15)
    mails = [
        replies.missing_info("Anna", "RFQ", "FRA", "ORD", ["weight_kg", "pickup_zone"], []),
        replies.dg_documents("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd", "msds"]),
        replies.dg_conflict("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd"]),
        replies.final_quote("Anna", "RFQ", "FRA", "ORD", "300", "Bravo Freight", "585.00",
                            "EUR", 5, ["air_all_in"], when),
        replies.acceptance_clarification("Anna", "RFQ", "Bravo Freight",
                                         "amount_differs_from_quote"),
        replies.no_offer("Anna", "RFQ", "FRA", "ORD", "no carrier returned a complete rate"),
        replies.booking_submitted("Anna", "RFQ", "Bravo Freight", "TM-1"),
        replies.booking_confirmed("Anna", "RFQ", "Bravo Freight", "BK-1", "FRA", "ORD", "300",
                                  "585.00", "EUR"),
        replies.booking_failed("Anna", "RFQ", "Bravo Freight", "the rate expired"),
        replies.tracking_answer("Anna", "RFQ", "BK-1", "confirmed", "Bravo Freight", None),
    ]
    for mail in mails:
        body = mail["body"]
        assert body.startswith("Hello Anna,") and body.endswith("FiveU Freight Desk")
        assert "!" not in body and len(body) < 700, mail["purpose"]
        assert not [phrase for phrase in banned if phrase in body.lower()], mail["purpose"]
    hold = mails[4]["body"]
    assert "have not booked anything yet" in hold and "EUR" not in hold
