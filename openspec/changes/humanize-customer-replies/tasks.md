# Tasks

## 1. Voice checklist (catches tells before anything else changes)

- [x] 1.1 Add `backend/tests/test_voice_checklist.py` with the D7 constant lists (forbidden characters, digits/currency, chatbot phrases, AI vocabulary, contrastive framing, upbeat closers, three-item lists, same first three words per purpose or framing id), checking every closing, framing and sign-off line of `reply_voice.md`, with each failure naming the line. Verify it fails on the current file, flagging at least "Hope that helps!", "Happy to help ..." and "Looking forward ...".
- [x] 1.2 Rewrite the closing lines in `reply_voice.md` so the checklist passes: replace the flagged lines, and give `first_quote`, `missing_info`, `dg_documents`, `quote_answer` and `booking_confirmed` at least two closings each that open differently. Update the file's guidance section with the checklist rules. Verify `make test` runs the checklist green and `tests/test_voice.py` still passes.

## 2. Purpose scope and stricter preservation

- [x] 2.1 In `voice.py`, add the explicit eligible-purpose set (D5) and return the body unchanged for `tracking_answer` and unknown purposes before any processing. Verify with a test that tracking answers (confirmed with and without update, and submitted) and an unknown purpose are byte-identical in warm mode.
- [x] 2.2 Replace the preservation check with paragraph classification (D4) and multiset number equality (D3). Verify with tests that a dropped fact paragraph, a reordered fact paragraph, a dropped number, an added number and a duplicated number each make the plain mail go out.

## 3. Framing rewrite

- [x] 3.1 Add the framing pattern table from D1 to `voice.py` and the `frame.<id>` entries to the voice-file loader. Ignore a variant that misses a group of its pattern or uses an unknown placeholder. Verify with tests that a missing `{w}` or `{route}` variant is never used and an unknown placeholder is skipped.
- [x] 3.2 Apply framing variants paragraph by paragraph with the stable choice from D6, before the closing line and sign-off. Verify with tests that a quote's intro can change while the price block, "To book ..." paragraph, PDF note and DG notes stay byte-identical, and that the same plain mail always gives the same warm mail.
- [x] 3.3 Write two or more framing variants per framing id in `reply_voice.md`, following the checklist (contractions, specific values, no chatbot phrases). Verify the checklist test passes, and add an example warm quote and DG request to the file's examples section, generated from the code rather than hand-written.
- [x] 3.4 Add a drift test that renders every eligible `replies.py` template with sample values and asserts each framing paragraph it produces matches a pattern in the table. Verify it fails if a framing sentence in `replies.py` is edited, by temporarily changing one locally.

## 4. Integration checks

- [x] 4.1 Run `make simulate SCENARIO=all SEED=42` with `REPLY_VOICE` unset and with `REPLY_VOICE=warm`, and compare every checked value across all scenarios. Verify zero differing values and the same pass/fail list in both runs.
- [x] 4.2 Run the full backend suite (`make test`) and `make lint`. Verify no test fails that does not also fail on `main`, and that the plain-mode output of every template is unchanged (the existing `test_plain_is_the_default_and_changes_nothing` and the sandbox tests).
- [ ] 4.3 Print one warm mail per eligible purpose from sample values and have a person read each aloud once. Verify by recording "read and approved" or the requested rewordings in the PR description.

## Workflow follow-up

- Operator: follow "Live run: human reply voice" in `docs/runbook.md` (set `REPLY_VOICE: "warm"`, rebuild, recreate the API and worker, check a quote, watch the next rounds, switch back to `plain` on any new rejection reason).
- Confirm which board column is the human-email +10 (header row), then compare rounds before and after the switch.
- Archive the change after review.
