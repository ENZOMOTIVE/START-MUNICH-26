# Spec Delta

## Purpose

Decides what kind every inbound mail is and which agent owns it, hands the mail to that agent, and keeps a record of how the decision was made.

## ADDED Requirements

### Requirement: Classify every inbound mail into one kind
The system SHALL assign every routed mail exactly one kind from: `rfq`, `acceptance`, `follow_up`, `carrier_bid`, `rate_desk_reply`, `tariff`, `tracking`, `booking_confirmation`, `unknown`.

#### Scenario: Mail is routed
- **WHEN** a routing job for a stored mail is processed
- **THEN** the mail's classification is one of the listed kinds

#### Scenario: Preset classification
- **WHEN** a mail was ingested with a classification already set to a known kind (as fixtures do)
- **THEN** that kind is kept and no further decision is made

### Requirement: Map each kind to one owning agent
The system SHALL assign `rfq`, `acceptance`, `follow_up`, `carrier_bid`, `rate_desk_reply` and `booking_confirmation` to the RFQ and booking agent, `tracking` to the track and trace agent, `tariff` to the tariff ingestion agent, and `unknown` to no agent.

#### Scenario: Tariff mail
- **WHEN** a mail is classified `tariff`
- **THEN** its owning agent is the tariff ingestion agent

#### Scenario: Unknown mail
- **WHEN** a mail is classified `unknown`
- **THEN** it has no owning agent and nothing is sent in response

### Requirement: Route known senders by address
The system SHALL classify mail from the tracking address, the rate desk address and the start-kit carrier list by sender address, attachments and subject alone, giving the same result for the same mail every time.

#### Scenario: Tracking question
- **WHEN** a mail's sender is the tracking address
- **THEN** it is classified `tracking`

#### Scenario: Rate desk answer
- **WHEN** a mail's sender is the rate desk address
- **THEN** it is classified `rate_desk_reply`

#### Scenario: Carrier bid
- **WHEN** a mail's sender is on the carrier list and the mail has no attachment
- **THEN** it is classified `carrier_bid`

#### Scenario: Tariff sheet
- **WHEN** a mail's sender is an airline on the carrier list and the mail carries an attachment
- **THEN** it is classified `tariff`

#### Scenario: Booking confirmation
- **WHEN** a carrier mail's subject matches the documented booking confirmation pattern
- **THEN** it is classified `booking_confirmation`

### Requirement: Route customer mail by thread position
The system SHALL treat mail from any other sender as customer mail, SHALL classify it `rfq` when it starts a thread, and SHALL classify it `follow_up` when it replies in a thread that has no quote awaiting an answer.

#### Scenario: New request
- **WHEN** a customer mail starts a new thread
- **THEN** it is classified `rfq`

#### Scenario: Reply after booking
- **WHEN** a customer replies in a thread whose shipment is already booked
- **THEN** it is classified `follow_up`

#### Scenario: Reply in an unknown thread
- **WHEN** a customer replies in a thread the system holds no request for
- **THEN** it is classified `follow_up`

### Requirement: Judge what a reply to a quote means
When a customer replies in a thread with a quote awaiting an answer, the system SHALL decide whether the reply accepts the quote. It SHALL classify the reply `acceptance` only on clear support, `follow_up` when the reply clearly asks something or declines, and `unknown` otherwise.

#### Scenario: Clear acceptance
- **WHEN** the customer replies "we accept your offer" to a quote
- **THEN** the reply is classified `acceptance`

#### Scenario: Acceptance in another language
- **WHEN** the customer replies "Wir nehmen das Angebot an. Bitte buchen." to a quote
- **THEN** the reply is classified `acceptance`

#### Scenario: Question about the quote
- **WHEN** the customer replies asking whether the price includes pickup
- **THEN** the reply is classified `follow_up` and no booking is started

#### Scenario: Conditional acceptance
- **WHEN** the customer accepts only on a condition, such as a pickup day
- **THEN** the reply is not classified `acceptance` and no booking is started

#### Scenario: Unclear reply
- **WHEN** the decision does not clearly support acceptance, a question or a decline
- **THEN** the reply is classified `unknown` and no booking is started

### Requirement: Ask the model only when the keyword check cannot confirm an acceptance
When the model step is enabled, the system SHALL classify a reply the keyword check accepts without a model call, and SHALL ask the model about every other reply to a quote.

#### Scenario: Keyword acceptance
- **WHEN** the model step is enabled and the keyword check finds a clear acceptance
- **THEN** the reply is classified `acceptance` and no model call is made

#### Scenario: Reply the keyword check cannot settle
- **WHEN** the model step is enabled and the keyword check does not find an acceptance
- **THEN** the model is asked once and its answer decides the kind

### Requirement: Keep routing without the decision model
The system SHALL use the keyword answer when the model step is disabled, times out, or returns an error, and SHALL never treat a failed model call as an answer.

#### Scenario: Model times out
- **WHEN** the model does not answer within its time limit
- **THEN** the reply is classified by the keyword check and the record names the fallback and the error

#### Scenario: Default configuration
- **WHEN** the system runs with the default configuration
- **THEN** routing makes no network request

### Requirement: Record how each mail was routed
The system SHALL store, with each routed mail, the chosen kind, the owning agent, what decided it (preset, sender rule, thread rule, decision model, or fallback), and for model decisions the model version and the probabilities it returned.

#### Scenario: Model decision
- **WHEN** a reply is classified by the decision model
- **THEN** the stored record shows the model version and the probability of each option

#### Scenario: Rule decision
- **WHEN** a mail is classified by a sender or thread rule
- **THEN** the stored record names the rule and contains no model call

### Requirement: Identify the sender in the routing record
For mail from a sender on the start-kit carrier list, the system SHALL store the carrier's name and whether it is an airline or a trucker with the routing record.

#### Scenario: Trucker bid
- **WHEN** a mail from a trucker on the carrier list is routed
- **THEN** the routing record names the trucker and marks the sender as a trucker

#### Scenario: Running inside the worker container
- **WHEN** the worker runs from its container image, which holds no start-kit folder
- **THEN** carrier senders are still recognised

### Requirement: Show unhandled replies on the session timeline
When a routed mail belongs to a thread that has a session and no handler runs for its kind, the system SHALL add an entry to that session's timeline naming the mail and its kind.

#### Scenario: Customer question after a quote
- **WHEN** a customer reply in a quoted thread is classified `follow_up`
- **THEN** the session timeline shows that a follow-up mail arrived

#### Scenario: Mail with no session
- **WHEN** a routed mail belongs to no session
- **THEN** no timeline entry is written and routing still succeeds

### Requirement: Dispatch each mail to its handler
The system SHALL invoke the registered handler for the mail's kind and SHALL complete routing without error for kinds that have no handler yet.

#### Scenario: Kind with a handler
- **WHEN** a mail is classified `rfq` or `acceptance`
- **THEN** the RFQ workflow or the booking workflow is started for it

#### Scenario: Kind without a handler
- **WHEN** a mail is classified with a kind that has no registered handler
- **THEN** the classification is stored, the routing job finishes successfully, and nothing is sent

#### Scenario: Mail routed twice
- **WHEN** the routing job for the same mail runs again
- **THEN** the same kind is stored and no second RFQ, session or booking is created
