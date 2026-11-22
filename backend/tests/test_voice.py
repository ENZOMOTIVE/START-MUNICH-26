"""The warm reply voice rewords framing sentences and adds a closing; nothing else changes."""

import logging
import re
from collections import Counter
from datetime import datetime

import pytest
from app.core.config import get_settings
from app.workflows import language, replies, voice

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
NAMES = ("Anna", "Ben", "Chen", "Dara", "Eli", "Fatima", "Gus", "Hana")
BOOK = "To book, reply to this email and confirm that you accept."


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


def _samples(name="Anna") -> dict[str, dict]:
    """One mail per eligible purpose, built by the real templates."""
    return {
        "missing_info": replies.missing_info(name, "RFQ", "FRA", "ORD", ["weight_kg"], []),
        "dg_documents": replies.dg_documents(
            name, "RFQ", "FRA", "ORD", ["UN3480"], ["dgd", "msds"]
        ),
        "dg_conflict": replies.dg_conflict(name, "RFQ", "FRA", "ORD", ["UN3480"], ["dgd"]),
        "first_quote": _quote(name),
        "quote_answer": replies.quote_answer(
            name, "RFQ", "Ibisair Cargo", 3, ["fuel_surcharge"], None, None, "FRA"
        ),
        "no_offer": replies.no_offer(name, "RFQ", "FRA", "BKK", "no rate on file"),
        "booking_confirmed": replies.booking_confirmed(
            name, "RFQ", "Ibisair Cargo", "BK-1", "FRA", "BKK", "468", "1234.50", "EUR"
        ),
        "booking_submitted": replies.booking_submitted(name, "RFQ", "Ibisair Cargo", "TM-1"),
        "acceptance_clarification": replies.acceptance_clarification(
            name, "RFQ", "Ibisair Cargo", "amount_differs_from_quote"
        ),
        "booking_failed": replies.booking_failed(name, "RFQ", "Ibisair Cargo", "no space"),
    }


# The framing sentences each template produces. If replies.py is edited so a sentence no longer
# matches voice.FRAMES, test_every_template_still_produces_its_frames fails.
EXPECTED_FRAMES = {
    "missing_info": ["thanks_request", "will_quote"],
    "dg_documents": ["thanks_request"],
    "dg_conflict": ["thanks_request"],
    "first_quote": ["quote_intro"],
    "quote_answer": ["thanks_question"],
    "no_offer": ["thanks_request", "check_again"],
    "booking_confirmed": ["pass_updates"],
    "booking_submitted": [],
    "acceptance_clarification": [],
    "booking_failed": [],
}


def _fact_paragraphs(plain: str) -> list[str]:
    return [p for p in plain.split("\n\n")[:-1] if voice._frame(p) is None]


def _assert_facts_kept(plain: str, warm: str) -> None:
    after, start = warm.split("\n\n"), 0
    for fact in _fact_paragraphs(plain):
        start = after.index(fact, start) + 1  # raises if missing or reordered
    assert Counter(NUMBER.findall(plain)) == Counter(NUMBER.findall(warm))


@pytest.fixture
def warm(monkeypatch):
    monkeypatch.setenv("REPLY_VOICE", "warm")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def plain(monkeypatch):
    monkeypatch.delenv("REPLY_VOICE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _voice(monkeypatch, frames=None, closing=None, sign_off=None):
    loaded = {"sign_off": sign_off or [], "closing": closing or {}, "frames": frames or {}}
    monkeypatch.setattr(voice, "load", lambda: loaded)


# ---------------------------------------------------------------- plain is the default


def test_plain_is_the_default_and_changes_nothing(plain):
    for name in (*NAMES, None):
        for purpose, mail in _samples(name).items():
            assert voice.apply(mail["body"], purpose, "en") == mail["body"], purpose


def test_the_setting_defaults_to_plain(plain):
    assert get_settings().reply_voice == "plain"


# ---------------------------------------------------------------- scope


def test_every_eligible_purpose_has_a_sample_and_a_closing():
    assert set(_samples()) == set(EXPECTED_FRAMES) == voice.ELIGIBLE
    assert all(voice.load()["closing"].get(purpose) for purpose in voice.ELIGIBLE)


def test_warm_leaves_other_languages_alone(warm):
    german = language.localize(_quote()["body"], "de")
    assert voice.apply(german, "first_quote", "de") == german
    for lang in ("fr", "es", "it", "nl"):
        body = language.localize(_quote()["body"], lang)
        assert voice.apply(body, "first_quote", lang) == body


def test_tracking_answers_and_unknown_purposes_stay_exactly_as_built(warm):
    for update in (None, {"status": "departed", "reported_at": datetime(2026, 11, 22, 8, 0)}):
        mail = replies.tracking_answer(
            "Anna", "RFQ", "BK-1", "confirmed", "Lumen Air Cargo", update
        )
        assert voice.apply(mail["body"], mail["purpose"], "en") == mail["body"]
    pending = replies.tracking_answer("Anna", "RFQ", "BK-1", "submitted", "Lumen Air Cargo", None)
    assert voice.apply(pending["body"], pending["purpose"], "en") == pending["body"]
    other = _quote()["body"]
    assert voice.apply(other, "some_new_purpose", "en") == other


def test_a_mail_replies_py_did_not_build_is_left_alone(warm):
    body = "Hello Anna,\n\nThank you for your question.\n\nSincerely,\nSomeone else"
    assert voice.apply(body, "quote_answer", "en") == body


# ---------------------------------------------------------------- what changes and what stays


def test_every_template_keeps_its_facts_and_numbers(warm):
    changed = 0
    for name in (*NAMES, None):
        for purpose, mail in _samples(name).items():
            body = voice.apply(mail["body"], purpose, "en")
            _assert_facts_kept(mail["body"], body)
            assert body.endswith("Kind regards,\nFiveU Air Freight Desk"), purpose
            changed += body != mail["body"]
    assert changed >= len(NAMES) * len(voice.ELIGIBLE)  # every eligible mail got a voice


def test_the_quote_keeps_its_price_block_and_booking_instruction(warm):
    mail = _quote()
    body = voice.apply(mail["body"], "first_quote", "en")
    paragraphs = body.split("\n\n")
    price = (
        "Total: EUR 1234.50 all-in\nCarrier: Ibisair Cargo, transit 3 days\n"
        "Included: fuel surcharge"
    )
    assert price in paragraphs
    assert paragraphs.index(BOOK) == paragraphs.index(price) + 1
    intro = paragraphs[1]
    assert intro != mail["body"].split("\n\n")[1]  # the introduction was reworded ...
    assert all(value in intro for value in ("468", "FRA", "BKK"))  # ... and still states the facts
    assert paragraphs[-1].startswith("Kind regards")
    assert paragraphs[-2] and paragraphs[-2] not in mail["body"]  # exactly one closing line


def test_pdf_and_document_notes_survive_byte_for_byte(warm):
    quote = _quote()["body"]
    with_pdf = quote.replace("\n\nTo book,", "\n\nThe offer is attached as a PDF.\n\nTo book,")
    warm_pdf = voice.apply(with_pdf, "first_quote", "en")
    assert f"The offer is attached as a PDF.\n\n{BOOK}" in warm_pdf
    note = "\n\nI could not open this attachment: dgd.pdf.\nThe declaration does not cover UN1263."
    dg = replies.dg_documents("Anna", "RFQ", "FRA", "ORD", ["UN3480"], ["dgd"])["body"]
    with_note = dg.replace("\n\nBest regards", f"{note}\n\nBest regards")
    warm_dg = voice.apply(with_note, "dg_documents", "en")
    assert note.strip() in warm_dg.split("\n\n")
    _assert_facts_kept(with_note, warm_dg)


def test_the_same_mail_always_gets_the_same_warm_mail(warm):
    plain_body = _quote()["body"]
    assert voice.apply(plain_body, "first_quote", "en") == voice.apply(
        plain_body, "first_quote", "en"
    )


def test_different_mails_get_different_variants(warm):
    intros = {
        voice.apply(_quote(total=str(t))["body"], "first_quote", "en").split("\n\n")[1]
        for t in (1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700)
    }
    assert len(intros) > 1


# ---------------------------------------------------------------- framing variants


def test_a_variant_missing_a_value_is_never_used(warm, monkeypatch):
    plain_body = _quote()["body"]
    variants = [
        "Offer for your shipment{route}:",  # drops the weight
        "Offer for your {w} kg{unknown}:",  # unknown placeholder
        "Offer for {w} kg shipment{route}:",  # carries everything
    ]
    _voice(monkeypatch, frames={"quote_intro": variants})
    assert voice.apply(plain_body, "first_quote", "en").split("\n\n")[1] == (
        "Offer for 468 kg shipment from FRA to BKK:"
    )
    _voice(monkeypatch, frames={"quote_intro": variants[:2]})
    assert voice.apply(plain_body, "first_quote", "en") == plain_body  # nothing usable: plain


def test_a_variant_must_carry_the_route_when_the_sentence_states_one(warm, monkeypatch):
    with_route = replies.missing_info("Anna", "RFQ", "FRA", "ORD", ["weight_kg"], [])["body"]
    without = replies.missing_info("Anna", "RFQ", None, None, ["weight_kg"], [])["body"]
    _voice(monkeypatch, frames={"thanks_request": ["Thanks for your request."]})
    assert voice.apply(with_route, "missing_info", "en") == with_route
    assert "Thanks for your request." in voice.apply(without, "missing_info", "en")


def test_the_greeting_without_a_name_gets_a_greeting_variant(warm):
    body = voice.apply(_quote(None)["body"], "first_quote", "en")
    assert body.split("\n\n")[0] in {"Hi there,", "Hello,"}


# ---------------------------------------------------------------- the safety net


def test_a_dropped_or_moved_fact_paragraph_means_the_plain_mail():
    plain_body = _quote()["body"]
    paragraphs = plain_body.split("\n\n")
    dropped = "\n\n".join(p for p in paragraphs if p != BOOK)
    swapped = "\n\n".join([paragraphs[0], paragraphs[2], paragraphs[1], *paragraphs[3:]])
    reordered = "\n\n".join(
        [paragraphs[0], paragraphs[1], paragraphs[3], paragraphs[2], paragraphs[4]]
    )
    for broken in (dropped, reordered):
        assert not voice._kept(plain_body, broken)
    assert swapped != plain_body  # (swapping the intro and the price block is also refused)
    assert not voice._kept(plain_body, swapped)


def test_a_dropped_added_or_duplicated_number_means_the_plain_mail():
    plain_body = _quote()["body"]
    intro = "Thank you for your request. Here is our offer for 468 kg from FRA to BKK:"
    assert intro in plain_body
    for new in (
        "Here is our offer from FRA to BKK:",  # dropped 468
        "Here is our offer for 468 kg from FRA to BKK, 2 pieces:",  # added 2
        "Here is our offer for 468 kg (468 kg) from FRA to BKK:",  # duplicated 468
    ):
        assert not voice._kept(plain_body, plain_body.replace(intro, new)), new
    assert voice._kept(plain_body, plain_body.replace(intro, "Here is our offer for 468 kg:"))


def test_a_hostile_voice_file_cannot_change_a_number(warm, monkeypatch):
    plain_body = _quote()["body"]
    _voice(monkeypatch, closing={"first_quote": ["Only 2 seats left."]}, sign_off=["Cheers"])
    assert voice.apply(plain_body, "first_quote", "en") == plain_body
    _voice(monkeypatch, frames={"quote_intro": ["{w} kg, yes {w} kg{route}:"]})
    assert voice.apply(plain_body, "first_quote", "en") == plain_body


def test_a_failure_in_the_voice_sends_the_plain_mail(warm, monkeypatch, caplog):
    def boom(body, purpose):
        raise RuntimeError("boom")

    monkeypatch.setattr(voice, "_warm", boom)
    plain_body = _quote()["body"]
    with caplog.at_level(logging.WARNING):
        assert voice.apply(plain_body, "first_quote", "en") == plain_body
    assert "reply voice failed" in caplog.text


# ---------------------------------------------------------------- templates and the voice file


@pytest.mark.parametrize("name", [*NAMES[:2], None])
def test_every_template_still_produces_its_frames(name):
    greeting = "greeting_named" if name else "greeting"
    for purpose, mail in _samples(name).items():
        found = voice.frames_in(mail["body"])
        assert found == [greeting, *EXPECTED_FRAMES[purpose]], purpose


def test_every_frame_has_variants_in_the_voice_file():
    loaded = voice.load()
    assert set(voice.FRAMES) == set(loaded["frames"])
    assert all(len(variants) >= 2 for variants in loaded["frames"].values())
    assert len(loaded["sign_off"]) == 2


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


def test_voice_file_lines_have_no_digits_or_currency():
    loaded = voice.load()
    lines = [
        *loaded["sign_off"],
        *(v for vs in loaded["closing"].values() for v in vs),
        *(v for vs in loaded["frames"].values() for v in vs),
    ]
    assert lines
    for line in lines:
        assert not re.search(r"\d|\b(?:EUR|USD)\b|€|\$", line, re.IGNORECASE), line


def test_a_line_with_a_price_is_ignored(tmp_path):
    path = tmp_path / "voice.md"
    path.write_text(
        "- first_quote: Only EUR 99 today!\n- sign-off: Cheers,\n"
        "- frame.greeting: Hi there,\n- frame.quote_intro: Offer for 5 {w} kg\n",
        encoding="utf-8",
    )
    assert voice.load(path) == {
        "sign_off": ["Cheers,"],
        "closing": {},
        "frames": {"greeting": ["Hi there,"]},
    }
