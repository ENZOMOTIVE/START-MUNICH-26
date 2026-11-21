# Spec Delta

## Purpose

Gives the system one place to call OpenAI models through the 5U gateway, so that every call is counted by 5U and recorded locally, and so that demo runs spend nothing.

## ADDED Requirements

### Requirement: Complete text through the 5U gateway
When live actions are allowed, the system SHALL send a chat completion request to the 5U gateway with the team key and SHALL return the generated text, the token usage, and the token probabilities when the gateway returns them.

#### Scenario: Successful completion
- **WHEN** a caller asks for a completion with messages and a model
- **THEN** the gateway is called once and the caller receives the text and the reported token usage

#### Scenario: Gateway error
- **WHEN** the gateway answers with an error or does not answer in time
- **THEN** the caller receives an error and no text is invented

### Requirement: Record every gateway call
The system SHALL store a usage record for every gateway call, naming the model and marking whether the call was simulated.

#### Scenario: Live call
- **WHEN** a live completion succeeds
- **THEN** a usage record for that model is stored and marked not simulated

### Requirement: No gateway spend in demo mode
When live actions are not allowed, the system SHALL NOT contact the gateway.

#### Scenario: Live adapter used while live is off
- **WHEN** the live gateway adapter is invoked while live actions are disabled
- **THEN** it refuses with the live-actions-disabled error and no network request is made
