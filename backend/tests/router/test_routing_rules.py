from dataclasses import replace
from pathlib import Path

import pytest
from app.db.models import MessageRow
from app.platform import carriers
from app.platform.decisions import (
    ACCEPT,
    DECLINE,
    QUESTION,
    UNCLEAR,
    KeywordJudge,
    ReplyContext,
    ReplyJudgment,
    keyword_verdict,
    reply_text,
)
from app.platform.router import classify
from app.platform.routing import MailFacts, decide

AIRLINE = "Aurelia-Air-Cargo@hackloop.mail"
TRUCKER_ROW = next(c for c in carriers.load_carriers().values() if c.kind == "trucker")


class FixedJudge:
    def __init__(self, verdict: str):
        self.verdict = verdict
        self.calls = 0

    def judge(self, reply: ReplyContext) -> ReplyJudgment:
        self.calls += 1
        return ReplyJudgment(verdict=self.verdict, decided_by="test", model="m", error=None)


def facts(**overrides) -> MailFacts:
    base = MailFacts(
        sender="Buyer <buyer@shipper.example>",
        subject="Quote request",
        body="Please quote",
        has_attachments=False,
        starts_thread=True,
    )
    return replace(base, **overrides)


def test_carrier_lookup_is_case_insensitive_and_knows_kinds():
    airline = carriers.carrier_for(AIRLINE)
    assert airline is not None and airline.kind == "airline"
    assert airline.name == "Aurelia Air Cargo"
    assert carriers.carrier_for(f"Dispatch <{TRUCKER_ROW.email.upper()}>").kind == "trucker"
    assert carriers.carrier_for("someone@shipper.example") is None


def test_bundled_carrier_list_used_when_repo_copy_missing(tmp_path: Path):
    path = carriers.carrier_list_path(tmp_path / "missing" / "carrier-list.csv")
    assert path == carriers.BUNDLED_CARRIER_LIST
    bundled = carriers.load_carriers(path)
    assert bundled[AIRLINE.lower()].kind == "airline"
    assert len(bundled) == len(carriers.load_carriers())


def test_tracking_address():
    d = decide(facts(sender="tracking@hackloop.mail"), FixedJudge(ACCEPT))
    assert (d.kind, d.decided_by, d.agent) == ("tracking", "sender_rule", "track_trace")


def test_rate_desk_address():
    d = decide(facts(sender="RateDesk@hackloop.mail", starts_thread=False), FixedJudge(ACCEPT))
    assert (d.kind, d.rule) == ("rate_desk_reply", "rate_desk_address")


def test_carrier_bid():
    d = decide(facts(sender=AIRLINE, starts_thread=False), FixedJudge(ACCEPT))
    assert (d.kind, d.sender["kind"], d.sender["name"]) == (
        "carrier_bid",
        "airline",
        "Aurelia Air Cargo",
    )


def test_trucker_bid_records_trucker():
    d = decide(facts(sender=TRUCKER_ROW.email, has_attachments=True), FixedJudge(ACCEPT))
    assert d.kind == "carrier_bid"
    assert d.sender["kind"] == "trucker"


def test_tariff_sheet():
    d = decide(facts(sender=AIRLINE, has_attachments=True), FixedJudge(ACCEPT))
    assert (d.kind, d.agent) == ("tariff", "tariff_ingestion")


def test_booking_confirmation_subject():
    d = decide(
        facts(sender=AIRLINE, subject="Re: Booking BK-AB12CD34 confirmed", starts_thread=False),
        FixedJudge(ACCEPT),
    )
    assert (d.kind, d.rule) == ("booking_confirmation", "booking_confirmation_subject")


def test_customer_new_thread_is_rfq():
    judge = FixedJudge(ACCEPT)
    d = decide(facts(), judge)
    assert (d.kind, d.decided_by, d.rule) == ("rfq", "thread_rule", "customer_new_thread")
    assert judge.calls == 0


@pytest.mark.parametrize("stage", [None, "intake", "pricing", "accepted", "booked", "exception"])
def test_customer_reply_without_open_quote_is_follow_up(stage):
    judge = FixedJudge(ACCEPT)
    d = decide(facts(starts_thread=False, thread_stage=stage), judge)
    assert (d.kind, d.decided_by) == ("follow_up", "thread_rule")
    assert judge.calls == 0


@pytest.mark.parametrize(
    ("verdict", "kind"),
    [(ACCEPT, "acceptance"), (QUESTION, "follow_up"), (DECLINE, "follow_up"), (UNCLEAR, "unknown")],
)
def test_reply_to_open_quote_uses_judge(verdict, kind):
    judge = FixedJudge(verdict)
    d = decide(facts(starts_thread=False, thread_stage="quoted"), judge)
    assert d.kind == kind
    assert d.decided_by == "test"
    assert judge.calls == 1


def test_preset_kind_wins_and_skips_judge():
    judge = FixedJudge(UNCLEAR)
    d = decide(facts(preset_kind="acceptance", starts_thread=False, thread_stage="quoted"), judge)
    assert (d.kind, d.decided_by) == ("acceptance", "preset")
    assert judge.calls == 0


def test_preset_unknown_is_not_a_preset():
    d = decide(facts(preset_kind="unknown"), FixedJudge(ACCEPT))
    assert d.kind == "rfq"


def test_sender_rules_ignore_body_text():
    d = decide(
        facts(sender="tracking@hackloop.mail", body="RFQ: we accept your offer"),
        FixedJudge(ACCEPT),
    )
    assert d.kind == "tracking"


@pytest.mark.parametrize(
    ("body", "verdict"),
    [
        ("We accept your offer, thanks.", ACCEPT),
        ("Please book it.", ACCEPT),
        ("Does the price include pickup?", QUESTION),
        ("Thanks, but we decline.", DECLINE),
        ("We cannot accept this price.", DECLINE),
        ("Thanks for the quote.", UNCLEAR),
        ("We accept. Can you confirm the pickup time?", ACCEPT),
    ],
)
def test_keyword_verdicts(body, verdict):
    assert keyword_verdict("Re: Quote", body) == verdict


@pytest.mark.parametrize(
    ("body", "verdict"),
    [
        # The eight probe replies.
        ("We accept your offer, please go ahead.", ACCEPT),
        ("OK with 412.50 EUR as long as pickup is on Monday morning.", UNCLEAR),
        ("Does the 412.50 include the pickup from our warehouse?", QUESTION),
        ("Thanks, but that is above our budget. We'll pass this time.", DECLINE),
        ("Vielen Dank, wir nehmen das Angebot an. Bitte buchen.", ACCEPT),
        ("Sounds good, let's do it. Could you send the booking reference once confirmed?", ACCEPT),
        ("Separate topic: we also have 80 kg CGN to DEL next week, can you quote that?", QUESTION),
        ("Thanks for the quick reply.", UNCLEAR),
        # Other languages.
        ("Wir akzeptieren den Preis.", ACCEPT),
        ("Einverstanden.", ACCEPT),
        ("Leider zu teuer für uns.", DECLINE),
        ("Wir nehmen das Angebot leider nicht an.", DECLINE),
        ("Nous acceptons votre offre, merci.", ACCEPT),
        ("C'est trop cher pour nous.", DECLINE),
        ("Aceptamos la oferta, por favor reserve.", ACCEPT),
        ("Accettiamo l'offerta.", ACCEPT),
        ("Akkoord, graag boeken.", ACCEPT),
        # Must never book.
        ("Va bene se ritiriamo lunedì?", QUESTION),
        ("Before we accept, does it include fuel?", QUESTION),
        ("Should we go ahead with the booking or wait?", QUESTION),
        ("We accept if you can do 380 EUR.", UNCLEAR),
        ("Can you give us a better deal?", QUESTION),
        ("Is dangerous goods accepted?", QUESTION),
        ("We don't accept this price.", DECLINE),
    ],
)
def test_keyword_verdicts_extended(body, verdict):
    assert keyword_verdict("Re: Quote FRA-LAX 250 kg", body) == verdict


def test_german_quoted_mail_is_ignored():
    body = "Bitte buchen.\n\nAm 21.11.2026 um 16:02 schrieb Desk <desk@x.de>:\n> Haben Sie Fragen?"
    assert reply_text(body) == "Bitte buchen."
    assert keyword_verdict("AW: Angebot", body) == ACCEPT


def test_keyword_ignores_quoted_earlier_mail():
    body = "Does this include fuel?\n\nOn Sat, 21 Nov 2026 Desk wrote:\n> please confirm, we accept"
    assert reply_text(body) == "Does this include fuel?"
    assert KeywordJudge().judge(ReplyContext("a@b.c", "Re: Quote", body)).verdict == QUESTION


def test_classify_still_importable_and_unchanged():
    row = MessageRow(
        external_id="x",
        thread_id="t",
        sender="buyer@example.com",
        subject="RE: RFQ FRA-LAX",
        body="we accept your offer",
        classification="unknown",
    )
    assert classify(row) == "acceptance"
