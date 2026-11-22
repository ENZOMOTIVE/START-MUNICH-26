"""Every line of the voice file is checked for AI-writing tells before it can reach a customer.

The file is read raw (not through voice.load, which silently drops lines it cannot use), so a
bad line fails here with its text. To add a tell, add it to the lists below. To fix a failure,
reword the line: there is no allow-list.
"""

import re
from collections import defaultdict
from pathlib import Path

VOICE_FILE = Path(__file__).parent.parent / "app" / "workflows" / "data" / "reply_voice.md"
_ENTRY = re.compile(r"^- (?P<key>[a-z_.-]+): (?P<text>.+?)\s*$")

KNOWN_PURPOSES = {
    "first_quote",
    "missing_info",
    "dg_documents",
    "dg_conflict",
    "quote_answer",
    "no_offer",
    "acceptance_clarification",
    "booking_submitted",
    "booking_confirmed",
    "booking_failed",
}
KNOWN_FRAMES = {
    "greeting_named",
    "greeting",
    "thanks_request",
    "quote_intro",
    "thanks_question",
    "will_quote",
    "pass_updates",
    "check_again",
}

FORBIDDEN_CHARS = {
    "—": "em dash",
    "–": "en dash",
    "--": "double hyphen",
    "‘": "curly quote",
    "’": "curly quote",
    "“": "curly quote",
    "”": "curly quote",
    "…": "ellipsis character",
}
DIGITS_OR_CURRENCY = re.compile(r"\d|\b(?:EUR|USD)\b|€|\$", re.IGNORECASE)
CHATBOT_PHRASES = (
    "hope this",
    "hope that",
    "hope you",
    "let me know if",
    "happy to help",
    "glad to help",
    "great question",
    "good question",
    "certainly",
    "absolutely",
    "feel free",
    "don't hesitate",
    "do not hesitate",
    "reach out",
    "reaching out",
    "touch base",
    "circle back",
    "as an ai",
)
AI_VOCABULARY = (
    "delve",
    "tapestry",
    "testament",
    "pivotal",
    "crucial",
    "vital",
    "foster",
    "cultivate",
    "showcase",
    "elevate",
    "empower",
    "harness",
    "holistic",
    "robust",
    "seamless",
    "effortless",
    "streamline",
    "supercharge",
    "unlock",
    "leverage",
    "utilize",
    "journey",
    "landscape",
    "realm",
    "synergy",
    "stakeholder",
    "game-changer",
    "world-class",
    "cutting-edge",
    "best-in-class",
    "very",
    "truly",
    "incredibly",
    "remarkably",
)
CONTRASTIVE = (
    re.compile(r"\bnot just\b", re.IGNORECASE),
    re.compile(r"\bisn't just\b", re.IGNORECASE),
    re.compile(r"\bnot only\b", re.IGNORECASE),
    re.compile(r"\bit's not\b.*\bit's\b", re.IGNORECASE),
    re.compile(r"\bit isn't\b.*\bit's\b", re.IGNORECASE),
)
UPBEAT_CLOSERS = (
    "looking forward",
    "exciting",
    "the future",
    "bright",
    "step in the right direction",
    "have a great day",
    "have a wonderful",
)
# "A, B and C" / "A, B, and C" / "A, B or C" with one-word items: the rule-of-three habit. Clause
# lists ("odd, ask and I'll explain it") are ordinary sentences and are not flagged: the third
# item has to end the clause.
THREE_ITEMS = re.compile(r"\b[\w'-]+, [\w'-]+,? (?:and|or) [\w'-]+(?=[.,;:!?]|$)")


def _entries():
    found = []
    for number, line in enumerate(VOICE_FILE.read_text(encoding="utf-8").splitlines(), 1):
        if m := _ENTRY.match(line):
            found.append((number, m["key"], m["text"]))
    return found


def _problems(text: str) -> list[str]:
    low = text.lower()
    problems = [f"{name} ({char!r})" for char, name in FORBIDDEN_CHARS.items() if char in text]
    if DIGITS_OR_CURRENCY.search(text):
        problems.append("digit or currency")
    problems += [f"chatbot phrase '{p}'" for p in CHATBOT_PHRASES if p in low]
    problems += [
        f"AI vocabulary '{w}'" for w in AI_VOCABULARY if re.search(rf"\b{re.escape(w)}", low)
    ]
    problems += [f"contrastive framing /{p.pattern}/" for p in CONTRASTIVE if p.search(text)]
    problems += [f"upbeat closer '{p}'" for p in UPBEAT_CLOSERS if p in low]
    if THREE_ITEMS.search(text):
        problems.append("three-item list")
    return problems


def test_the_voice_file_has_entries():
    keys = {key for _, key, _ in _entries()}
    assert "sign-off" in keys
    assert "first_quote" in keys


def test_every_entry_key_is_known():
    valid = {"sign-off", *KNOWN_PURPOSES, *(f"frame.{f}" for f in KNOWN_FRAMES)}
    unknown = [(n, key) for n, key, _ in _entries() if key not in valid]
    assert not unknown, f"keys that would never be used (typo?): {unknown}"


def test_no_line_has_an_ai_writing_tell():
    failures = [
        f"line {number} [{key}] {text!r}: {', '.join(found)}"
        for number, key, text in _entries()
        if (found := _problems(text))
    ]
    assert not failures, "\n" + "\n".join(failures)


def test_variants_of_one_purpose_open_differently():
    seen = defaultdict(list)
    for number, key, text in _entries():
        seen[(key, " ".join(text.lower().split()[:3]))].append(number)
    repeated = {k: v for k, v in seen.items() if len(v) > 1 and k[0] != "sign-off"}
    assert not repeated, f"same first three words within one key: {repeated}"


def test_the_checklist_itself_catches_the_known_tells():
    # Guards the guard: each tell below must be flagged, or the lists above went stale.
    for line in (
        "Hope that helps!",
        "I'm happy to help with anything else.",
        "Looking forward to your reply.",
        "It isn't just a quote, it's a plan.",
        "Whether timing, routing or pickup, just ask.",
        "Our robust process — as promised.",
        "Pay EUR 99 today.",
        "Please feel free to ask.",
    ):
        assert _problems(line), line
    assert not _problems("If anything here is unclear, just reply and I'll explain it.")


def test_the_checklist_knows_the_same_purposes_and_frames_as_the_code():
    from app.workflows import voice

    assert KNOWN_PURPOSES == set(voice.ELIGIBLE)
    assert KNOWN_FRAMES == set(voice.FRAMES)
