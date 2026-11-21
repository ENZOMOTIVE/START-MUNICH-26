# Pricing capability

## ADDED Requirements

### Requirement: Tariff ingestion with evidence
The system SHALL ingest every supplied tariff set into versioned structured
records, retaining the original file, its checksum, effective date and the
fields each value was derived from. An unreadable or ambiguous rule SHALL
become a visible review exception and SHALL NOT be replaced by an invented value.

#### Scenario: All sources accounted for
- **WHEN** ingestion runs over the starter kit
- **THEN** every tariff set has either approved structured records or a visible
  review exception, and no file is silently skipped

#### Scenario: Scanned tariff
- **WHEN** a tariff is only available as a JPG scan
- **THEN** it is OCR-read into records with evidence, or raised as a review
  exception if the scan cannot be read confidently

### Requirement: Portal rate retrieval
The system SHALL retrieve live portal rates preserving carrier, lane, currency,
`rate_id`, `valid_until`, `dataset_version`, transit and FX evidence, recover
from bounded 429/503 responses, and SHALL NOT present an expired rate as
currently bookable.

#### Scenario: One portal source times out
- **WHEN** one lane's portal call times out or is rate-limited
- **THEN** the remaining sources still return rates and the failure is recorded

### Requirement: Deterministic all-in pricing
The system SHALL compute totals using `Decimal`, applying each source's units,
weight breaks, minimum charges, billed-weight rules and rounding, and SHALL add
the required pickup leg exactly once. Chargeable weight SHALL be
`max(gross_kg, volume_m3 × 167)` unless a tariff states its own divisor.

#### Scenario: Weight-break pivot
- **WHEN** paying for the next bracket's minimum weight at its lower per-kg rate
  is cheaper than the applicable bracket's total
- **THEN** the engine selects the cheaper total

#### Scenario: Reproducible price
- **WHEN** the same inputs and source version are priced twice
- **THEN** the same total is produced, and earlier committed decisions keep
  their original evidence after a tariff update

### Requirement: RateCandidate contract for booking
The system SHALL emit eligible `RateCandidate` records containing total,
currency, charge breakdown, service, expiry, source links and any available
booking identifier. A record missing a required charge, currency, validity or
conversion rule SHALL NOT be marked complete.

#### Scenario: Handoff to booking
- **WHEN** a validated RFQ fixture is priced
- **THEN** T3 receives eligible RateCandidate records sufficient to quote and
  book without re-deriving any price

#### RateCandidate fields (implemented in `pricing/models.py`, `to_dict()` JSON)
- `carrier`, `carrier_code`, `lane{origin,destination}`, `source` (`portal` | `tariff` | `bid`)
- `service{transit_days, tags[cheapest|fastest]}`
- `total` (all-in EUR string, 2 dp, incl. pickup; null unless complete), `currency` = `EUR`
- `air_total` (air leg EUR), `source_total` + `source_currency` + `fx_eur_per_unit` (carrier's own price)
- `charges[]`: `{code: air_freight|fuel|security|air_all_in|pickup, description, amount, amount_exact,
  currency, basis_kg, rate, evidence}`
- `chargeable_weight_kg`, `billed_weight_kg`, `break_kg`, `pivoted`
- `rate_id` (portal only), `valid_until` (ISO, UTC), `dataset_version` (portal `v1` or tariff `set@hash`)
- `complete`, `missing[]` (e.g. `pickup`, `fx:USD`, `expired`, `valid_until`), `bookable`, `eligible`
- `source_links[]`, `evidence{}` (portal response / tariff pivot options / validity)
