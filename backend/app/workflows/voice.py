"""A warmer voice for English customer mails, written in data/reply_voice.md.

Off unless REPLY_VOICE=warm. It only adds: one closing line per mail purpose and a new sign-off.
A purpose with no usable closing line is left exactly as built (no new sign-off either).
Every paragraph and every number of the plain mail stays, in order, and no new number appears;
otherwise the plain mail is returned. Lines with a digit or a currency are ignored, so the voice
can never state a price. Placeholders ({origin}, {destination}, {carrier}) are filled only with
values the plain mail already states; a line whose placeholder cannot be filled is skipped.
"""

import hashlib
import re
from functools import lru_cache
from pathlib import Path

from app.core.config import get_settings

VOICE_FILE = Path(__file__).parent / "data" / "reply_voice.md"
_ENTRY = re.compile(r"^- (?P<key>[a-z_-]+): (?P<text>.+?)\s*$")
_FORBIDDEN = re.compile(r"\d|\b(?:EUR|USD)\b|€|\$", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ROUTE = re.compile(r" from ([A-Z]{3}) to ([A-Z]{3})\b")
_CARRIER = (  # where replies.py states the carrier
    re.compile(r"^Carrier: ([^,\n]+),", re.MULTILINE),  # final_quote
    re.compile(r"\bkg from [A-Z]{3} to [A-Z]{3} with ([^,\n]+),"),  # booking_confirmed
    re.compile(r"\bbooking with (.+?) under our reference\b"),  # booking_submitted
)


@lru_cache
def load(path: Path = VOICE_FILE) -> dict:
    """{"sign_off": [lines], "closing": {purpose: [variants]}} from the markdown file."""
    sign_off: list[str] = []
    closing: dict[str, list[str]] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"sign_off": [], "closing": {}}
    for line in text.splitlines():
        if not (m := _ENTRY.match(line)) or _FORBIDDEN.search(m["text"]):
            continue
        if m["key"] == "sign-off":
            sign_off.append(m["text"])
        else:
            closing.setdefault(m["key"], []).append(m["text"])
    return {"sign_off": sign_off, "closing": closing}


def facts(body: str) -> dict[str, str]:
    """Route and carrier as the plain mail states them; nothing the mail does not say."""
    found: dict[str, str] = {}
    if m := _ROUTE.search(body):
        found["origin"], found["destination"] = m[1], m[2]
    for pattern in _CARRIER:
        if m := pattern.search(body):
            carrier = m[1].strip()
            if carrier and len(carrier) <= 40:
                found["carrier"] = carrier
            break
    return found


def closing_for(variants: list[str], body: str) -> str | None:
    """One variant, picked by the mail itself (the same mail always gets the same line)."""
    known = facts(body)
    usable = []
    for text in variants:
        if any(name not in known for name in _PLACEHOLDER.findall(text)):
            continue
        filled = _PLACEHOLDER.sub(lambda m: known[m[1]], text)
        if not _FORBIDDEN.search(filled):
            usable.append(filled)
    if not usable:
        return None
    return usable[int(hashlib.sha256(body.encode()).hexdigest(), 16) % len(usable)]


def _kept(plain: str, warm: str) -> bool:
    """Every paragraph but the sign-off, in order, and every number of `plain` is in `warm`,
    and `warm` has no number that `plain` does not have."""
    rest = warm.split("\n\n")
    for paragraph in plain.split("\n\n")[:-1]:
        if paragraph not in rest:
            return False
        rest = rest[rest.index(paragraph) + 1 :]
    before, after = _NUMBER.findall(plain), _NUMBER.findall(warm)
    return all(n in after for n in before) and all(n in before for n in after)


def apply(body: str, purpose: str, lang: str) -> str:
    """`body` in the warm voice when enabled and English; otherwise `body` unchanged."""
    if get_settings().reply_voice != "warm" or lang != "en":
        return body
    voice = load()
    paragraphs = body.split("\n\n")
    if len(paragraphs) < 2:
        return body
    closing = closing_for(voice["closing"].get(purpose, []), body)
    if closing is None:
        return body  # no line for this purpose (tracking answers, for one): exactly as built
    sign_off = "\n".join(voice["sign_off"]) or paragraphs[-1]
    warm = "\n\n".join([*paragraphs[:-1], closing, sign_off])
    return warm if _kept(body, warm) else body
