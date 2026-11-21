# Spec Delta

## Purpose

Brings every mail sent to the team mailbox into the system exactly once, attached to its thread, and queues it for routing.

## ADDED Requirements

### Requirement: Poll the team mailbox with a persisted cursor
When live actions are allowed, the system SHALL repeatedly fetch mail newer than its stored cursor from the competition mailbox, oldest first, and SHALL advance the cursor only after the fetched mail is stored.

#### Scenario: New mail arrives
- **WHEN** the mailbox holds mail with ids above the stored cursor
- **THEN** each mail is stored and the cursor moves to the value the mailbox returned for the next poll

#### Scenario: Restart after a crash
- **WHEN** the worker stops after storing mail but before advancing the cursor, and then restarts
- **THEN** the same mail is fetched again and no duplicate message or duplicate routing job is created

#### Scenario: Live actions are off
- **WHEN** the system runs in demo mode or with live actions disabled
- **THEN** no request is made to the competition mailbox

### Requirement: Store each mail exactly once
The system SHALL store each inbound mail under its mailbox id and SHALL queue exactly one routing job per stored mail.

#### Scenario: Same mail ingested twice
- **WHEN** a mail with an already stored mailbox id is ingested again
- **THEN** the existing message is returned and no new routing job is queued

### Requirement: Keep the original mail payload
The system SHALL keep the mailbox's payload for each mail unmodified alongside any data it derives, and SHALL read only fields the mailbox documents or that were confirmed against a real response.

#### Scenario: Payload has undocumented fields
- **WHEN** a polled mail contains fields the system does not know
- **THEN** the mail is stored with those fields preserved and intake does not fail

### Requirement: Group mail into threads
The system SHALL assign every inbound mail a thread identifier equal to the first mail of its reference chain, or to its own id when it starts a thread, and SHALL record whether the mail starts a thread.

#### Scenario: Reply in an existing thread
- **WHEN** a mail's references list starts with the id of an earlier mail
- **THEN** both mails carry the same thread identifier and the later one is recorded as a reply

#### Scenario: First mail of a thread
- **WHEN** a mail has no references
- **THEN** its thread identifier is its own mailbox id and it is recorded as starting a thread

### Requirement: Give agents access to attachments
The system SHALL keep each attachment's id, filename, content type and size with the stored mail, and SHALL return an attachment's bytes on request when live actions are allowed.

#### Scenario: Mail with a tariff sheet
- **WHEN** a stored mail lists an attachment and an agent requests it
- **THEN** the agent receives the attachment's bytes and filename

#### Scenario: Attachment requested in demo mode
- **WHEN** an attachment is requested while live actions are disabled
- **THEN** the request is refused with the live-actions-disabled error and no network request is made

### Requirement: Expose the reply target for each mail
The system SHALL store each inbound mail's mailbox id so that an agent can address a reply to it.

#### Scenario: Agent replies to a customer
- **WHEN** an agent prepares a reply to a stored mail
- **THEN** the mail's stored mailbox id is the value the mailbox accepts as the reply target

### Requirement: Survive mailbox errors
The system SHALL keep polling after a failed poll and SHALL respect rate limiting from the mailbox.

#### Scenario: Mailbox returns an error
- **WHEN** a poll fails with a network error or a server error
- **THEN** the cursor is unchanged, the error is logged, and the next poll is attempted

#### Scenario: Rate limited
- **WHEN** the mailbox answers a poll with HTTP 429
- **THEN** the system waits before polling again and loses no mail
