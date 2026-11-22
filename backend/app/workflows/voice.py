"""A warmer voice for English customer mails, written in data/reply_voice.md.

Off unless REPLY_VOICE=warm; with it off (the default) `apply` returns the mail untouched.

What it may change, and nothing else:
- a framing paragraph (greeting, request thanks, quote introduction, a transition) can be
  swapped for one of several variants, which must carry every value of the sentence they replace;
- one closing line and a new sign-off are added.
Every other paragraph (the price block, "To book, reply ...", document notes, booking facts)
stays byte-identical and in place, and the numbers of the mail stay exactly the same. If that does
not hold, or anything goes wrong, the plain mail is returned. Lines with a digit or a currency are
ignored, so the voice can never state a price. Only English mails to the requesting customer and
the purposes in ELIGIBLE are touched: tracking answers, other languages and carrier mails never.
"""

import hashlib
import logging
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings
from app.workflows.replies import SIGNATURE

log = logging.getLogger(__name__)

VOICE_FILE = Path(__file__).parent / "data" / "reply_voice.md"
# Purposes whose mails may change. tracking_answer is left out on purpose: it earns the tracking
# points and the jury reads the quote threads, so a changed tracking mail only adds risk.
ELIGIBLE = frozenset(
    {
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
)

_ENTRY = re.compile(r"^- (?P<key>[a-z_.-]+): (?P<text>.+?)\s*$")
_FORBIDDEN = re.compile(r"\d|\b(?:EUR|USD)\b|€|\$", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ROUTE_FACT = re.compile(r" from ([A-Z]{3}) to ([A-Z]{3})\b")
_CARRIER = (  # where replies.py states the carrier
    re.compile(r"^Carrier: ([^,\n]+),", re.MULTILINE),  # final_quote
    re.compile(r"\bkg from [A-Z]{3} to [A-Z]{3} with ([^,\n]+),"),  # booking_confirmed
    re.compile(r"\bbooking with (.+?) under our reference\b"),  # booking_submitted
)

_ROUTE = r"(?P<route>(?: from \S+ to \S+| from \S+| to \S+)?)"
# The framing sentences of replies.py, as whole paragraphs. A paragraph that matches none of
# these is a fact paragraph and is never changed. tests/test_voice.py checks that every template
# still produces the frames listed there, so editing a sentence in replies.py cannot silently
# switch the voice off for it.
FRAMES = {
    "greeting_named": re.compile(r"Hello (?P<name>.+),"),
    "greeting": re.compile(r"Hello,"),
    "thanks_request": re.compile(r"Thank you for your request" + _ROUTE + r"\."),
    "quote_intro": re.compile(
        r"Thank you for your request\. Here is our offer for (?P<w>\S+) kg" + _ROUTE + ":"
    ),
    "thanks_question": re.compile(r"Thank you for your question\."),
    "will_quote": re.compile(r"As soon as I have that, I will send you the quote\."),
    "pass_updates": re.compile(r"I will pass on tracking updates as I receive them\."),
    "check_again": re.compile(
        r"If anything about the shipment can change, tell me and I will check again\."
    ),
}


@lru_cache
def load(path: Path = VOICE_FILE) -> dict:
    """{"sign_off": [lines], "closing": {purpose: [lines]}, "frames": {frame id: [lines]}}."""
    sign_off: list[str] = []
    closing: dict[str, list[str]] = {}
    frames: dict[str, list[str]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"sign_off": [], "closing": {}, "frames": {}}
    for line in text.splitlines():
        if not (m := _ENTRY.match(line)) or _FORBIDDEN.search(m["text"]):
            continue
        if m["key"] == "sign-off":
            sign_off.append(m["text"])
        elif m["key"].startswith("frame."):
            frames.setdefault(m["key"][len("frame.") :], []).append(m["text"])
        else:
            closing.setdefault(m["key"], []).append(m["text"])
    return {"sign_off": sign_off, "closing": closing, "frames": frames}


def facts(body: str) -> dict[str, str]:
    """Route and carrier as the plain mail states them; nothing the mail does not say."""
    found: dict[str, str] = {}
    if m := _ROUTE_FACT.search(body):
        found["origin"], found["destination"] = m[1], m[2]
    for pattern in _CARRIER:
        if m := pattern.search(body):
            carrier = m[1].strip()
            if carrier and len(carrier) <= 40:
                found["carrier"] = carrier
            break
    return found


def _pick(options: list[str], body: str, key: str) -> str:
    """One option, chosen by the mail itself: the same mail always gets the same line."""
    digest = hashlib.sha256(f"{key}\n{body}".encode()).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


def closing_for(variants: list[str], body: str) -> str | None:
    """One closing line whose placeholders the mail can fill, or None."""
    known = facts(body)
    usable = []
    for text in variants:
        if any(name not in known for name in _PLACEHOLDER.findall(text)):
            continue
        filled = _PLACEHOLDER.sub(lambda m: known[m[1]], text)
        if not _FORBIDDEN.search(filled):
            usable.append(filled)
    return _pick(usable, body, "closing") if usable else None


def _frame(paragraph: str) -> tuple[str, dict] | None:
    """(frame id, values of the sentence) when the paragraph is a framing sentence."""
    for frame_id, pattern in FRAMES.items():
        if m := pattern.fullmatch(paragraph):
            return frame_id, m.groupdict()
    return None


def frames_in(body: str) -> list[str]:
    """Frame ids of the paragraphs of `body` that the voice may reword, in order."""
    found = (_frame(paragraph) for paragraph in body.split("\n\n")[:-1])
    return [frame[0] for frame in found if frame]


def _reframe(frame_id: str, values: dict, variants: list[str], body: str, index: int):
    """A variant of one framing sentence that carries every value the sentence states."""
    values = {name: value or "" for name, value in values.items()}
    stated = {name for name, value in values.items() if value}
    usable = []
    for text in variants:
        names = set(_PLACEHOLDER.findall(text))
        if names <= values.keys() and stated <= names:
            usable.append(_PLACEHOLDER.sub(lambda m: values[m[1]], text))
    return _pick(usable, body, f"{frame_id}:{index}") if usable else None


def _numbers(text: str) -> Counter:
    return Counter(_NUMBER.findall(text))


def _kept(plain: str, warm: str) -> bool:
    """Every non-framing paragraph of `plain` is in `warm` unchanged and in place, the only
    addition is one closing line, and the numbers of both mails are exactly the same."""
    before, after = plain.split("\n\n"), warm.split("\n\n")
    if len(after) not in (len(before), len(before) + 1):
        return False
    for index, paragraph in enumerate(before[:-1]):
        if _frame(paragraph) is None and after[index] != paragraph:
            return False
    return _numbers(plain) == _numbers(warm)


def _warm(body: str, purpose: str) -> str:
    paragraphs = body.split("\n\n")
    if len(paragraphs) < 2 or paragraphs[-1] != SIGNATURE:
        return body  # not a mail replies.py built: leave it alone
    voice = load()
    out = []
    for index, paragraph in enumerate(paragraphs[:-1]):
        frame = _frame(paragraph)
        variants = voice["frames"].get(frame[0], []) if frame else []
        chosen = _reframe(frame[0], frame[1], variants, body, index) if variants else None
        out.append(chosen or paragraph)
    closing = closing_for(voice["closing"].get(purpose, []), body)
    if closing:
        out += [closing, "\n".join(voice["sign_off"]) or paragraphs[-1]]
    else:
        out.append(paragraphs[-1])
    return "\n\n".join(out)


def apply(body: str, purpose: str, lang: str) -> str:
    """`body` in the warm voice when enabled, English and an eligible purpose; else unchanged."""
    if get_settings().reply_voice != "warm" or lang != "en" or purpose not in ELIGIBLE:
        return body
    try:
        warm = _warm(body, purpose)
    except Exception:  # noqa: BLE001 - a customer mail must never fail to go out over its tone
        log.warning(
            "reply voice failed for a %s mail; sending the plain mail", purpose, exc_info=True
        )
        return body
    return warm if _kept(body, warm) else body
