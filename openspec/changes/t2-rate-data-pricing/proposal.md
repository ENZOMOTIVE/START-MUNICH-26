# T2 — Rate data & pricing

## Why
The forwarder can only quote correctly if every tariff source is read
accurately and every all-in total is computed deterministically. Pricing
accuracy is 45 of the 100 points per accepted quote, and gateway cost (40
points) forbids using an LLM to price. This package turns the 10 tariff
sets and the live portal into one trustworthy pricing contract that T3 can
consume without re-deriving anything.

## What changes
- **Tariff ingestion**: parse all 10 tariff sets across 11 files (TXT, XLSX,
  PDF, DOCX, JPG scans) into versioned structured records, keeping originals,
  checksums, effective dates and field-level evidence. Anything unreadable or
  ambiguous becomes a visible review exception — never an invented value.
- **Portal client**: discover lanes/airlines and fetch live rates, preserving
  carrier, lane, currency, `rate_id`, `valid_until`, `dataset_version`,
  transit and FX evidence, with bounded 429/503 recovery.
- **Pricing engine**: compute all-in totals with `Decimal` using each
  source's own units, weight breaks, minimum charges, billed-weight rules and
  rounding, adding the required pickup leg exactly once.
- **RateCandidate contract**: the single output consumed by T3.

## Out of scope
- RFQ parsing, carrier-bid email exchange, offer selection and booking (T3).
- Mailbox polling, router, gateway adapter, outbox/sender (T1).
- CRM, tracking, Sessions UI (T4).

## Impact
- Affected specs: `pricing` (new capability)
- Interfaces: consumes T1 contracts + validated RFQ fixture; emits
  `RateCandidate` records to T3.
