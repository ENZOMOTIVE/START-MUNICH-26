"""The warm reply voice only adds lines; the plain mail is untouched unless REPLY_VOICE=warm."""

import re

import pytest
from app.core.config import get_settings
from app.workflows import language, replies, voice

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def _quote(name="Anna", total="1234.50"):
    return replies.final_quote(
        name,
        "RFQ FRA-BKK",
        "FRA",
        "BKK",
        "468",
        "Ibisair Cargo",
        total,
        "EUR",
        3,
        ["fuel_surcharge"],
        None,
    )


@pytest.fixture
def warm(monkeypatch):
    monkeypatch.setenv("REPLY_VOICE", "warm")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_plain_is_the_default_and_changes_nothing():
    get_settings.cache_clear()
    mail = _quote()
    assert voice.apply(mail["body"], "first_quote", "en") == mail["body"]


def test_warm_keeps_every_paragraph_and_number_and_adds_a_closing(warm):
    for name in ("Anna", "Ben", "Chen", "Dara", "Eli", None):  # several variants get picked
        mail = _quote(name)
        body = voice.apply(mail["body"], "first_quote", "en")
        assert body != mail["body"]
        for paragraph in mail["body"].split("\n\n")[:-1]:
            assert paragraph in body
        assert NUMBER.findall(body) == NUMBER.findall(mail["body"])
        assert "\n\nTo book," in body  # the PDF note and the acceptance instruction anchor here
        assert body.endswith("Kind regards,\nFiveU Air Freight Desk")


def test_the_same_mail_always_gets_the_same_line(warm):
    body = _quote()["body"]
    assert voice.apply(body, "first_quote", "en") == voice.apply(body, "first_quote", "en")


def test_warm_leaves_other_languages_alone(warm):
    german = language.localize(_quote()["body"], "de")
    assert voice.apply(german, "first_quote", "de") == german


def test_placeholders_take_only_what_the_mail_states():
    body = _quote()["body"]
    assert voice.facts(body) == {"origin": "FRA", "destination": "BKK", "carrier": "Ibisair Cargo"}
    assert voice.closing_for(["Safe trip to {destination}."], body) == "Safe trip to BKK."
    # Not stated in the mail (or misspelt): the line is skipped, never filled with a guess.
    no_route = replies.booking_failed("Anna", "RFQ", "Ibisair Cargo", "no space")["body"]
    assert voice.closing_for(["Safe trip to {destination}."], no_route) is None
    assert voice.closing_for(["Safe trip to {destinaton}.", "Thanks!"], body) == "Thanks!"


def test_booking_mails_find_their_carrier():
    confirmed = replies.booking_confirmed(
        "Anna", "RFQ", "Polaris Air Logistics", "BK-1", "STR", "LAX", "1841", "2000.00", "EUR"
    )["body"]
    submitted = replies.booking_submitted("Anna", "RFQ", "Lumen Air Cargo", "TM-1")["body"]
    assert voice.facts(confirmed)["carrier"] == "Polaris Air Logistics"
    assert voice.facts(submitted) == {"carrier": "Lumen Air Cargo"}


def test_tracking_answers_and_unknown_purposes_stay_exactly_as_built(warm):
    for update in (
        None,
        {"status": "departed", "reported_at": __import__("datetime").datetime(2026, 11, 22, 8, 0)},
    ):
        mail = replies.tracking_answer(
            "Anna", "RFQ", "BK-1", "confirmed", "Lumen Air Cargo", update
        )
        assert voice.apply(mail["body"], mail["purpose"], "en") == mail["body"]
    pending = replies.tracking_answer("Anna", "RFQ", "BK-1", "submitted", "Lumen Air Cargo", None)
    assert voice.apply(pending["body"], pending["purpose"], "en") == pending["body"]
    plain = _quote()["body"]
    assert voice.apply(plain, "some_new_purpose", "en") == plain


def test_voice_file_has_no_digits_or_currency_and_covers_the_quote():
    loaded = voice.load()
    assert loaded["closing"]["first_quote"]
    lines = [*loaded["sign_off"], *(v for vs in loaded["closing"].values() for v in vs)]
    for line in lines:
        assert not re.search(r"\d|\b(?:EUR|USD)\b|€|\$", line, re.IGNORECASE), line


def test_a_line_with_a_price_is_ignored(tmp_path):
    path = tmp_path / "voice.md"
    path.write_text("- first_quote: Only EUR 99 today!\n- sign-off: Cheers,\n", encoding="utf-8")
    assert voice.load(path) == {"sign_off": ["Cheers,"], "closing": {}}


def test_a_new_number_makes_the_plain_mail_go_out():
    plain = _quote()["body"]
    sneaky = plain.replace("\n\nBest regards", "\n\nOnly 2 seats left!\n\nBest regards")
    assert not voice._kept(plain, sneaky)
