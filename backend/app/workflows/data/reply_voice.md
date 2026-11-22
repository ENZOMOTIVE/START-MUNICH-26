# Reply voice

The tone of our customer emails, for the "+10 most human email" jury pick. Read by
`app/workflows/voice.py` when `REPLY_VOICE=warm`. With the default `REPLY_VOICE=plain` this
file is not used and every mail goes out exactly as `replies.py` builds it.

## Rules (the code enforces 1 to 4)

1. **Add, never replace.** A line here is added to the mail. The facts, the price block and
   the "To book, reply ..." instruction stay word for word, so pricing, booking, the PDF offer
   and the acceptance check behave exactly as before.
2. **No digits, no currency.** A line must not contain a number, `EUR`, `USD`, `€` or `$`.
   Prices and references come only from the workflow. The code skips any line that breaks
   this rule, and refuses a mail that would gain a number the plain mail does not have.
3. **English only.** Mails in German, French, Spanish, Italian or Dutch are translated line by
   line from a fixed table (`language.py`). A new line would stay in English there, and that
   is the `wrong_language` rejection (rounds 70 and 71). Those mails keep the plain voice.
4. **If anything is lost, the plain mail goes out.** If the warm mail is missing any line or
   number of the plain mail, the plain mail is sent instead.
5. **Placeholders only for facts the mail states.** `{origin}`, `{destination}` and
   `{carrier}` are copied from the mail itself. If the mail does not state one, that line is
   skipped for that mail, and another line of the same purpose is used.

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
  reads the same sentence.

Not borrowed, on purpose:

- Deliberate typos or "strategic imperfection": a priced quote with mistakes looks careless.
- Invented anecdotes ("a client of ours saved ..."): they would be facts nobody recorded.
- Scarcity or curiosity hooks ("only a few spots left", "a weird trick"): they could read as
  a conditional or changing offer, and the price must stay the only firm statement.
- Promises the workflow does not keep: calls, discounts, dates, "I'll check personally".
- Thanking twice: the plain mail already opens with a thank-you.

## Sign-off

Replaces the last paragraph of a mail that gets a closing line ("Best regards, FiveU Freight
Desk"). One entry per line of the sign-off. Keep team names out: the jury reads the mails with team names hidden.

- sign-off: Kind regards,
- sign-off: FiveU Air Freight Desk

## Closing lines

Added just before the sign-off. Several lines per purpose: one is picked per mail. A purpose
with no line here (or none usable for that mail) is sent exactly as built, with its original
sign-off. `tracking_answer` is left out on purpose: those mails earn the tracking points, the
jury reads the accepted quote threads, and a changed tracking mail gains nothing and risks that.

- first_quote: I hope this works well for your {origin} to {destination} shipment. If anything's unclear, just hit reply and I'll walk you through it.
- first_quote: If you'd like me to go through any of the charges, just reply here. I'm happy to help.
- first_quote: Looking forward to getting your cargo on its way to {destination}.
- missing_info: Sorry for the extra step! Once I have this, I can give you a firm price rather than a rough estimate.
- missing_info: I know it's one more thing to dig out, but it means the price you get is the price you pay.
- dg_documents: I know the paperwork is the least fun part of shipping dangerous goods, but airlines won't load the cargo without it, and I'd like yours to fly without a hitch.
- dg_documents: Sorry for the extra paperwork. It's the one part of dangerous goods we can't skip, and it keeps your shipment from being held at the airport.
- dg_conflict: Thanks for bearing with me here. Getting this right now saves you any surprises at the airport.
- dg_conflict: A quick yes or no is all I need, and then I'll get your price out.
- acceptance_clarification: I just want to be sure we book exactly what you need.
- quote_answer: Hope that helps! If anything else comes up, just reply here.
- quote_answer: Happy to help with anything else you'd like to know.
- no_offer: I'm sorry I can't help with a price this time. I'd rather be upfront with you than send a figure I can't stand behind.
- booking_submitted: Thanks for choosing us for this one. I'll keep a close eye on it for you.
- booking_submitted: Thanks for going ahead with {carrier}. I'll keep a close eye on it for you.
- booking_confirmed: Thanks for trusting us with this shipment. If you need anything before it flies, I'm just a reply away.
- booking_confirmed: Wishing your cargo a smooth trip to {destination}. If anything comes up before departure, just reply here.
- booking_failed: I'm really sorry for the hassle.

## Example answers (for people, not read by the code)

A warm English quote. Everything above the closing line is the unchanged plain mail.

```text
Hello Anna,

Thank you for your request. Here is our offer for 468 kg from FRA to BKK:

Total: EUR 1234.50 all-in
Carrier: Ibisair Cargo, transit 3 days
Included: fuel surcharge and security surcharge

To book, reply to this email and confirm that you accept.

I hope this works well for your FRA to BKK shipment. If anything's unclear, just hit reply and I'll walk you through it.

Kind regards,
FiveU Air Freight Desk
```

A request for dangerous-goods papers:

```text
Hello Marco,

Thank you for your request from FRA to ORD.

Your message mentions UN3480, which ships as dangerous goods. Before I can quote, I need the Shipper's Declaration for Dangerous Goods (DGD) and the safety data sheet (MSDS).

Please reply to this email with the documents attached and I will continue right away.

I know the paperwork is the least fun part of shipping dangerous goods, but airlines won't load the cargo without it, and I'd like yours to fly without a hitch.

Kind regards,
FiveU Air Freight Desk
```
