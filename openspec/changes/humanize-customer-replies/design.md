# Design

## Context

- Customer mails are built by `replies.py` as paragraphs joined by blank lines: greeting, body paragraphs, sign-off. `rfq.outbound` is the single place where a customer mail is finished. It adds the PDF note (anchored on `"\n\nTo book,"`), translates with `language.localize` (a table of exact English lines → other languages, values copied over), then calls `voice.apply` (PR #43). DG notes are inserted earlier, while the mail is built (anchored on `"\n\nBest regards"`).
- `voice.apply` today only appends a closing line and swaps the sign-off. It falls back to the plain mail if a paragraph or number goes missing. Its content comes from `data/reply_voice.md`.
- Carrier and trucker mails never reach `voice.apply` (no customer source in `outbound`). Tracking answers do reach it. Today they stay plain only because they have no closing line configured.
- Live runs `LLM_MODE=fake`. There is no model to steer, so every human touch has to be fixed text.

## Goals / Non-Goals

**Goals:**
- Give the framing around the facts a human voice, for English customer mails only.
- Make "facts unchanged" a property the code checks on every mail, not a convention.
- Catch AI-writing tells in the voice file at test time, before they reach a customer.

**Non-Goals:**
- Rewording any fact-bearing or instruction paragraph (price block, "To book ...", "Please reply ... with the documents attached ...", "To price it I still need ...", DG notes, booking status sentences, acceptance clarifications).
- Changing `replies.py`, `language.py` or the plain output in any way.
- Personas with invented names, anecdotes, or any statement the workflow did not record.

## Decisions

### D1. Framing patterns live in code, wording lives in the markdown file
`voice.py` holds a small table of framing ids, each with the exact plain pattern it replaces, as a full-match regex with named groups, the same technique as `language._LINES`. `reply_voice.md` holds the variants per framing id (`- frame.quote_intro: Here's what I can offer for your {w} kg shipment{route}:`).
- *Why*: the patterns are coupled to `replies.py` wording and must be tested together with it. The wording is what people want to edit and review.
- *Alternative*: patterns in the markdown file. Rejected because a wrong pattern edited by hand would silently match nothing, or the wrong paragraph.

Initial framing ids (each applies wherever the plain paragraph occurs):

| id | plain paragraph | groups |
|---|---|---|
| `greeting_named` | `Hello {name},` | name |
| `greeting` | `Hello,` | none |
| `thanks_request` | `Thank you for your request{route}.` | route |
| `quote_intro` | `Thank you for your request. Here is our offer for {w} kg{route}:` | w, route |
| `thanks_question` | `Thank you for your question.` | none |
| `will_quote` | `As soon as I have that, I will send you the quote.` | none |
| `pass_updates` | `I will pass on tracking updates as I receive them.` | none |
| `check_again` | `If anything about the shipment can change, tell me and I will check again.` | none |

The quote intro is matched as a paragraph of its own (`final_quote` joins it as its own paragraph). The price block that follows is a separate paragraph and is never a framing candidate.

### D2. Whitelist rewrite, not "anything without a digit"
Only paragraphs that fully match a framing pattern can change.
- *Why*: several fact-bearing paragraphs contain no digit (DG notes naming files, "Your message describes the cargo as non-dangerous ...", acceptance clarifications). A digit-based rule would let them be reworded.
- *Alternative*: rewrite whole templates per purpose. Rejected because it duplicates `replies.py`, so every template change would need two edits, and the plain/warm drift would not be caught.

### D3. Values carried by named groups, verified twice
A variant is usable only if it contains every `{group}` of its pattern, and no unknown placeholder. Values are copied from the matched plain paragraph, never scanned from elsewhere. After assembly, the mail's numbers must equal the plain mail's numbers as a multiset (stricter than PR #43's set check, which a repeated number could pass).
- The existing `{origin}`/`{destination}`/`{carrier}` closing placeholders keep working: they are filled from the plain body (PR #43 `facts`) and stay digit-free.

### D4. Preservation check by paragraph classification
The plain mail's paragraphs are classified as *framing* (matched a pattern), *sign-off* (last paragraph) or *fact* (everything else). The warm mail must contain all fact paragraphs byte-identical and in the same order. Framing paragraphs may differ only by being replaced at the same position. Otherwise the plain mail is sent. This covers the PDF note and DG notes automatically, since they are fact paragraphs.

### D5. Explicit purpose scope
Eligible purposes: `first_quote`, `missing_info`, `dg_documents`, `dg_conflict`, `quote_answer`, `no_offer`, `acceptance_clarification`, `booking_submitted`, `booking_confirmed`, `booking_failed`. `tracking_answer` and any unknown purpose are returned unchanged before any processing. This replaces PR #43's "no closing means plain" rule as the reason tracking answers stay untouched. Language stays as today: only `en`.

### D6. Stable choice
Each choice (one per framing paragraph, one per closing) picks `sha256(plain body + choice key) mod n` over the usable variants. The same mail always renders the same way (spec "Variant choice is stable per mail"), retries and sandbox runs are reproducible, and different customers see different wording.

### D7. Checklist test as data
`tests/test_voice_checklist.py` loads every line of the voice file and checks it against constant lists in the test:
- forbidden characters (em dash, en dash used as a dash, `--`, curly quotes)
- digits and currency
- chatbot phrases ("hope this helps", "hope that helps", "let me know if", "happy to help", "great question", "certainly", "feel free to")
- AI vocabulary from the shared checklist (delve, seamless, leverage, robust, elevate, unlock, journey, landscape, pivotal, crucial, ...)
- contrastive framing (`not just`, `isn't just`, `it's not ... it's`)
- generic upbeat closers ("looking forward", "exciting", "the future")
- three-item lists (`A, B and C` / `A, B or C` within one line)
- the same first three words within one purpose or framing id

Failures name the line.
- *Why constants in the test*: the list changes when someone finds a new tell, and a test file is where a reviewer looks for "what is enforced".
- *Trade-off*: the three-item regex is an approximation and may flag a legitimate phrase. The fix is rewording, not an allow-list, to keep the rule simple.

### D8. Sign-off unchanged in mechanism
The sign-off swap stays as in PR #43, applied when a closing line is used. The sign-off text stays free of team names (the jury reads mails with names hidden) and of invented personal names.

## Risks / Trade-offs

- [5U's grader or simulated customers may key on exact plain phrases such as "Here is our offer"] → The sandbox comparison (all scenarios, voice on vs off, every checked value identical) is required before merge. The live switch stays an operator decision: watch the first rounds' verdicts, and flip back to `plain` on any new rejection reason.
- [The jury effect can't be measured offline] → Rollout is reversible per round. The variant used is stored in each outbound action's body, so picks can be matched to wording once the board column is known.
- [A checklist pushed too hard produces a caricature voice (noted in the shared post's discussion)] → Variants stay short and plain. A person reads every variant aloud once before the live switch (task).
- [Rule-of-three and opener checks may reject reasonable lines] → Reword the line. The check stays strict.
- [Framing patterns drift from `replies.py` wording] → A test renders every eligible template and asserts its framing paragraphs match a pattern, so a template edit that breaks a pattern fails CI instead of silently disabling the voice.

## Migration Plan

1. Merge. Nothing changes in live while `REPLY_VOICE` is unset or `plain`.
2. Operator: on the live machine set `REPLY_VOICE: "warm"` in `compose.live.yaml`, rebuild the image (the voice file is baked in) and recreate the API and worker containers.
3. Watch the next rounds: verdicts and accuracy unchanged, and the human-email column.
4. Rollback: set `REPLY_VOICE: "plain"` (or remove it) and recreate the containers. No rebuild or data change is needed.

## Open Questions

- Which board column is the human-email +10? Rounds 56, 107 and 108 show a +10 outside the tracking column, but the header row is not captured. This decides how the effect is read after rollout, not how the change is built.
