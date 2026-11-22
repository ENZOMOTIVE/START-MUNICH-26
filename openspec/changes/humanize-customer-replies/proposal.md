# Proposal

## Why

Every accepted round can earn "+10 most human email", picked by a jury reading all teams' mails with names hidden. Our customer mails come from fixed templates (`replies.py`): the same "Thank you for your request." opener in every mail type, form-letter transitions, and a label-style price block. The opt-in warm voice from PR #43 only appends a closing line and a sign-off, and a review of its lines against a published human-writing checklist found chatbot artifacts ("Hope that helps!"), rule-of-three padding and generic upbeat closers. The framing around the facts is where a human voice has to come from, and it is untouched today.

## What Changes

- Clean up `backend/app/workflows/data/reply_voice.md`: replace closing lines that match known AI-writing tells, and give each purpose several closings that open differently.
- Add a **framing rewrite** to the warm voice. For English customer mails only, a whitelisted set of framing sentences can be swapped for one of several human variants: greetings, "Thank you for your request..." openers, and "As soon as I have that..." style transitions. Values a framing sentence carries (name, weight, route) are copied over verbatim. Every other paragraph, including the price block, the "To book, reply ..." instruction, DG notes and acceptance wording, stays byte-identical.
- Extend the warm voice's safety check. If any non-framing paragraph changed or moved, or the set of numbers differs, the plain mail is sent.
- Add an **offline voice checklist test**. Every variant in the voice file is checked for banned tells (em dashes, double hyphens, curly quotes, chatbot artifacts, corporate and AI vocabulary, contrastive "not X, it's Y" framing, three-item lists, generic upbeat closers), for digits or currency, and for variants of one purpose that open with the same words. A tell fails the test suite instead of reaching the jury.
- Unchanged: plain voice stays the default, and with `REPLY_VOICE=plain` mails are byte-identical to today. Other languages, tracking answers and mails to carriers or truckers are never touched. No LLM calls, no pricing, booking or acceptance changes.

Out of scope:
- Enabling `REPLY_VOICE=warm` in `compose.live.yaml`. That's an operator decision made on the live machine.
- Translating the warm voice into other languages.
- Any runtime model polishing.

## Capabilities

### New Capabilities
- `customer-reply-voice`: how the tone of outbound customer mails may vary from the plain templates, which mails and paragraphs it may touch, what it must preserve, and the content rules its wording must meet.

### Modified Capabilities
(none: no specs exist yet; the warm voice from PR #43 is captured in the new capability)

## Impact

- Code: `backend/app/workflows/voice.py` (framing rewrite, stricter preservation check), `backend/app/workflows/data/reply_voice.md` (closings, framing variants, checklist), `backend/tests/test_voice.py` (framing and preservation tests), new `backend/tests/test_voice_checklist.py`.
- No change to `replies.py`, `language.py`, `rfq.py` call sites, pricing, booking, the PDF offer, or the sandbox simulator. The existing hook in `rfq.outbound` stays the single entry point.
- Operations: has an effect only after `REPLY_VOICE=warm` is set and the live image is rebuilt (the voice file is baked into the image).
- Measurement: the board shows the human-email +10 per round, but which column it is (rounds 56, 107 and 108 show an unexplained +10) is not yet confirmed. See the open question in design.md.
