# Spec Delta

## Purpose

Lets the wording of outbound customer mails sound like a person wrote them, without changing any fact, price, instruction or language the plain templates state, so the team can compete for the human-email jury bonus at no risk to accuracy, booking or cost points.

## ADDED Requirements

### Requirement: Plain voice is the default
With the voice setting absent or set to plain, every outbound customer mail SHALL be sent exactly as the templates and the translation step produce it.

#### Scenario: Setting absent
- **WHEN** a quote, clarification, booking or tracking mail is sent and the voice setting is not configured
- **THEN** its body is byte-identical to the body the templates and translation produce

#### Scenario: Setting plain
- **WHEN** the voice setting is `plain`
- **THEN** no closing line, framing variant or sign-off change appears in any mail

### Requirement: Warm voice scope
With the warm voice enabled, only English mails to the requesting customer SHALL be eligible for a changed voice. Tracking answers, mails in any other language and mails to carriers, rate desks or truckers SHALL be sent exactly as in plain mode.

#### Scenario: Non-English customer
- **WHEN** the warm voice is enabled and the customer wrote in German, French, Spanish, Italian or Dutch
- **THEN** the mail is the translated plain mail with no English line added or substituted

#### Scenario: Tracking answer
- **WHEN** the warm voice is enabled and a tracking answer is sent
- **THEN** the tracking answer is identical to the plain tracking answer

#### Scenario: Carrier enquiry
- **WHEN** the warm voice is enabled and a rate or pickup enquiry goes to a carrier or trucker
- **THEN** that mail is identical to the plain mail

### Requirement: Framing rewrite is limited to known framing sentences
The warm voice SHALL change only paragraphs that exactly match a known framing sentence (greeting, request-thanks opener, quote introduction, "as soon as I have that" style transition). A framing variant SHALL carry over every value of the sentence it replaces (name, weight, route) verbatim.

#### Scenario: Quote introduction rewritten
- **WHEN** an English quote opens with "Thank you for your request. Here is our offer for 468 kg from FRA to BKK:"
- **THEN** the warm mail may use a different introduction, and that introduction contains "468", "FRA" and "BKK" exactly as stated

#### Scenario: Unknown paragraph untouched
- **WHEN** a paragraph is not a known framing sentence (for example a DG note naming files, or an acceptance clarification)
- **THEN** it appears in the warm mail byte-identical and in the same position

#### Scenario: Variant missing a value
- **WHEN** a framing variant does not contain every value of the sentence it would replace
- **THEN** that variant is not used for the mail

### Requirement: Fact paragraphs are preserved
In a warm mail, every paragraph that is not a framing sentence or the sign-off SHALL appear unchanged and in its original order, including the price block, the "To book, reply ..." instruction and any note added for PDF offers or documents.

#### Scenario: Price block and booking instruction
- **WHEN** an English quote is sent with the warm voice
- **THEN** the "Total:", "Carrier:", "Included:", "Pickup:" and "Valid until:" lines and the "To book, reply to this email and confirm that you accept." paragraph are byte-identical to the plain mail

#### Scenario: PDF offer note
- **WHEN** the customer asked for a PDF offer and the warm voice is enabled
- **THEN** the PDF note paragraph is present exactly as in plain mode and the attached PDF is built from the final warm body

### Requirement: Numbers never change
A warm mail SHALL contain exactly the numbers of the plain mail, with no number missing and no new number added. Otherwise the plain mail SHALL be sent.

#### Scenario: Lost number falls back
- **WHEN** applying the warm voice would drop or alter any number of the plain mail
- **THEN** the plain mail is sent unchanged

#### Scenario: New number falls back
- **WHEN** applying the warm voice would add a number the plain mail does not contain
- **THEN** the plain mail is sent unchanged

### Requirement: Closing line and sign-off
A warm mail whose purpose has a usable closing line SHALL end with one closing line followed by the configured sign-off. A purpose with no usable closing line SHALL keep its plain ending.

#### Scenario: Quote closing
- **WHEN** an English quote is sent with the warm voice
- **THEN** exactly one closing line precedes the configured sign-off, after the booking instruction

#### Scenario: No closing configured
- **WHEN** no closing line is configured or usable for a mail's purpose
- **THEN** the mail keeps the plain sign-off

### Requirement: Variant choice is stable per mail
For a given plain mail, the warm voice SHALL always produce the same warm mail. Different mails MAY receive different variants.

#### Scenario: Same mail twice
- **WHEN** the warm voice is applied twice to the same plain mail
- **THEN** both results are identical

### Requirement: Voice wording rules
Every closing line, framing variant and sign-off line SHALL contain no digit, no currency, no em dash or double hyphen and no curly quote, and SHALL avoid the listed AI-writing tells (chatbot phrases, AI vocabulary, contrastive "not X, it's Y" framing, three-item lists, generic upbeat closers). A violation SHALL fail the automated test suite.

#### Scenario: Chatbot phrase rejected
- **WHEN** a closing line such as "Hope that helps!" is added to the voice file
- **THEN** the voice checklist test fails and names the offending line

#### Scenario: Price-like content ignored at runtime
- **WHEN** a voice line contains a digit or currency
- **THEN** the line is never used in a mail

### Requirement: Variants of one purpose open differently
Within one purpose, no two closing lines and no two framing variants of the same framing sentence SHALL start with the same three words.

#### Scenario: Repeated opener rejected
- **WHEN** two quote closings both start with "If you'd like"
- **THEN** the voice checklist test fails
