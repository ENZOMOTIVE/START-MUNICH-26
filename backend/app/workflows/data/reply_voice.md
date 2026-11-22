# Reply voice

The tone of our customer emails, for the "+10 most human email" jury pick. Read by
`app/workflows/voice.py` when `REPLY_VOICE=warm`. With the default `REPLY_VOICE=plain` this
file is not used and every mail goes out exactly as `replies.py` builds it.

## Rules (the code enforces 1 to 4, the tests enforce 6)

1. **Facts stay word for word.** A closing line is added, and a framing sentence (see below) can
   be reworded. The price block, the "To book, reply ..." instruction, document notes and every
   other paragraph stay byte-identical, so pricing, booking, the PDF offer and the acceptance
   check behave exactly as before.
2. **No digits, no currency.** A line must not contain a number, `EUR`, `USD`, `€` or `$`.
   Prices and references come only from the workflow. The code skips any line that breaks
   this rule, and refuses a mail that would gain a number the plain mail does not have.
3. **English only.** Mails in German, French, Spanish, Italian or Dutch are translated line by
   line from a fixed table (`language.py`). A new line would stay in English there, and that
   is the `wrong_language` rejection (rounds 70 and 71). Those mails keep the plain voice.
4. **If anything is lost, the plain mail goes out.** If the warm mail is missing or has moved a
   fact paragraph, or its numbers differ in any way, the plain mail is sent instead.
5. **Placeholders only for facts the mail states.** `{origin}`, `{destination}` and
   `{carrier}` are copied from the mail itself. If the mail does not state one, that line is
   skipped for that mail, and another line of the same purpose is used.
6. **No AI-writing tells.** `tests/test_voice_checklist.py` reads this file and fails on: chatbot
   phrases ("hope this helps", "happy to help", "let me know if", "feel free"), AI vocabulary
   ("seamless", "leverage", "journey", "robust" and the like), "not just X, it's Y" framing,
   one-word three-item lists, upbeat closers ("looking forward"), em dashes, curly quotes, and
   two lines of one purpose that start with the same three words. Reword the line; there is no
   allow-list. To add a newly spotted tell, add it to the lists in that test.

## What makes a mail read as human (what we borrow, and what we do not)

Borrowed from how good cold emails are written:

- **Specific, not generic.** Name their route or carrier ("your FRA to BKK shipment") rather
  than "your request".
- **Empathy for the real annoyance.** Dangerous-goods paperwork, a missing detail, no price
  available: say you know it is a hassle, and why it matters for *their* cargo.
- **Talk like a person.** Contractions ("I'll", "don't", "you're"), short sentences next to
  longer ones, no memo phrases ("please be advised", "we hereby", "I hope this email finds you
  well").
- **Focus on them.** "your cargo", "what you need", not "our solutions".
- **Honest and calm.** No pushy urgency. Saying plainly what we cannot do builds trust.
- **Variety.** Several lines per purpose. The code picks one per mail, so not every customer
  reads the same sentence. Lines of one purpose also differ in length and shape.

Not borrowed, on purpose:

- Deliberate typos or "strategic imperfection": a priced quote with mistakes looks careless.
- Invented anecdotes ("a client of ours saved ..."): they would be facts nobody recorded.
- Scarcity or curiosity hooks ("only a few spots left", "a weird trick"): they could read as
  a conditional or changing offer, and the price must stay the only firm statement.
- Promises the workflow does not keep: calls, discounts, dates, "I'll check personally".
- Thanking twice: the plain mail already opens with a thank-you.
- Engagement bait ("Thoughts?", "Curious what you think"): a reply is only useful when it
  books, asks about the offer or sends what we asked for.

## Sign-off

Replaces the last paragraph of a mail that gets a closing line ("Best regards, FiveU Freight
Desk"). One entry per line of the sign-off. Keep team names out: the jury reads the mails with
team names hidden.

- sign-off: Kind regards,
- sign-off: FiveU Air Freight Desk

## Framing sentences

These replace one plain sentence of an English mail, in its own place. Only these sentences can
be replaced, and each id matches exactly one sentence of `replies.py` (`FRAMES` in `voice.py`).
A variant must carry every value its sentence states, written as a placeholder, or it is not
used for that mail. Several variants per id: one is picked per mail.

| id | plain sentence | placeholders |
|---|---|---|
| `greeting_named` | Hello Anna, | `{name}` |
| `greeting` | Hello, | none |
| `thanks_request` | Thank you for your request from FRA to ORD. | `{route}` (" from FRA to ORD", may be empty) |
| `quote_intro` | Thank you for your request. Here is our offer for 468 kg from FRA to BKK: | `{w}` (weight), `{route}` |
| `thanks_question` | Thank you for your question. | none |
| `will_quote` | As soon as I have that, I will send you the quote. | none |
| `pass_updates` | I will pass on tracking updates as I receive them. | none |
| `check_again` | If anything about the shipment can change, tell me and I will check again. | none |

- frame.greeting_named: Hi {name},
- frame.greeting_named: Hello {name},
- frame.greeting: Hi there,
- frame.greeting: Hello,
- frame.thanks_request: Thanks for sending over your request{route}.
- frame.thanks_request: Your request{route} came in, thank you.
- frame.thanks_request: Thanks for your request{route}.
- frame.quote_intro: Thanks for your request. Here is what we can offer for your {w} kg shipment{route}:
- frame.quote_intro: Thank you for sending this over. Here's our offer for your {w} kg shipment{route}:
- frame.quote_intro: Your request is in, and here is our offer for {w} kg{route}:
- frame.thanks_question: Thanks for asking.
- frame.thanks_question: Thanks for the question.
- frame.will_quote: Once I have that, your quote is on its way.
- frame.will_quote: Send it over and I'll get the quote out to you.
- frame.pass_updates: I'll pass on tracking updates as soon as I get them.
- frame.pass_updates: Any tracking updates I receive, I'll forward to you.
- frame.check_again: If something about the shipment can change, tell me and I'll check again.
- frame.check_again: If the shipment details can change, just tell me and I'll take another look.

## Closing lines

Added just before the sign-off. Several lines per purpose: one is picked per mail. A purpose
with no line here (or none usable for that mail) is sent exactly as built, with its original
sign-off. `tracking_answer` is left out on purpose: those mails earn the tracking points, the
jury reads the accepted quote threads, and a changed tracking mail gains nothing and risks that.

- first_quote: If anything here is unclear, just reply and I'll explain it.
- first_quote: Take your time with the offer. If a charge looks odd, ask and I'll go through it with you.
- first_quote: A short reply is all it takes to book your {origin} to {destination} shipment.
- missing_info: Sorry for the extra step. I'd rather ask than guess at the price.
- missing_info: I know it's one more thing to dig out, but it keeps the price accurate.
- missing_info: One quick reply with those details and I can get started on the price.
- dg_documents: I know the paperwork is the least fun part of dangerous goods, but airlines won't load the cargo without it.
- dg_documents: Sorry for the extra paperwork. It's the one step we can't skip with dangerous goods.
- dg_documents: Once those arrive, the price is next.
- dg_conflict: Getting this right now saves any surprise at the airport.
- dg_conflict: A quick yes or no is all I need, then your price is next.
- acceptance_clarification: I just want to be sure we book exactly what you need.
- acceptance_clarification: Better to check now than to book the wrong thing.
- quote_answer: If anything else comes up, just reply here.
- quote_answer: Anything else about the offer, just ask.
- no_offer: I'm sorry I can't help with a price this time. I'd rather be upfront with you than send a figure I can't stand behind.
- no_offer: Sorry I can't give you a price yet. I'd rather say so than leave you waiting.
- booking_submitted: Thanks for choosing us for this one. I'll keep a close eye on it.
- booking_submitted: Thanks for going ahead with {carrier}. I'll be watching for their confirmation.
- booking_confirmed: Thanks for trusting us with this shipment. If you need anything before it flies, just reply here.
- booking_confirmed: Wishing your cargo a smooth trip to {destination}. If anything comes up before departure, just reply here.
- booking_confirmed: Thanks for letting us handle your {origin} to {destination} shipment.
- booking_failed: I'm sorry for the hassle with this one.

## Example answers (for people, not read by the code)

Generated from the real templates with `REPLY_VOICE=warm` (see the script note in the PR).
Facts, the price block and the booking instruction are the unchanged plain mail; only the
greeting, the opening sentence and the last lines are in the warm voice.

A quote:

```text
Hi Anna,

Thank you for sending this over. Here's our offer for your 468 kg shipment from FRA to BKK:

Total: EUR 1234.50 all-in
Carrier: Ibisair Cargo, transit 3 days
Included: fuel surcharge and security surcharge

To book, reply to this email and confirm that you accept.

Take your time with the offer. If a charge looks odd, ask and I'll go through it with you.

Kind regards,
FiveU Air Freight Desk
```

A request for dangerous-goods papers:

```text
Hello Marco,

Thanks for your request from FRA to ORD.

Your message mentions UN3480, which ships as dangerous goods. Before I can quote, I need the Shipper's Declaration for Dangerous Goods (DGD) and the safety data sheet (MSDS).

Please reply to this email with the documents attached and I will continue right away.

I know the paperwork is the least fun part of dangerous goods, but airlines won't load the cargo without it.

Kind regards,
FiveU Air Freight Desk
```

A booking confirmation:

```text
Hi Anna,

Your booking is confirmed. Booking reference: BK-1.

468 kg from FRA to BKK with Ibisair Cargo, EUR 1234.50 all-in.

I'll pass on tracking updates as soon as I get them.

Thanks for letting us handle your FRA to BKK shipment.

Kind regards,
FiveU Air Freight Desk
```

