# T2 — Implementation tasks

## 1. Scaffold & contracts
- [ ] 1.1 Create `pricing/` module and `models.py` (RateCandidate, ChargeLine, ReviewException, Evidence)
- [ ] 1.2 `money.py` Decimal helpers (quantize, rounding, FX apply)
- [ ] 1.3 Agree RateCandidate shape with T3; write it into `specs/pricing`

## 2. Tariff ingestion (all 11 files)
- [ ] 2.1 `tariffs/ingest.py`: walk `starter-kit/`, checksum + effective date + version each source
- [ ] 2.2 TXT reader (yellowtail)
- [ ] 2.3 XLSX reader (elbflug, kestrel, vega, wayzata)
- [ ] 2.4 PDF reader (cobalt-skylift, helix, orion)
- [ ] 2.5 DOCX reader (quetzal)
- [ ] 2.6 OCR reader for JPG scans (danube-wings p1+p2)
- [ ] 2.7 Unreadable/ambiguous -> ReviewException (never invent a value)
- [ ] 2.8 `store.py` versioned store; re-ingest keeps earlier committed evidence

## 3. Portal client
- [ ] 3.1 `/v1/portal/ports` + `/brackets` discovery
- [ ] 3.2 `/v1/portal/rates` fetch; preserve rate_id, valid_until, dataset_version, transit, FX
- [ ] 3.3 Bounded 429/503 retry; one slow port never stalls the rest
- [ ] 3.4 Expired rates not presented as bookable

## 4. Pricing engine
- [ ] 4.1 Chargeable weight = max(gross, m3*167) per source rules
- [ ] 4.2 Weight-break pivot (min across applicable + higher brackets)
- [ ] 4.3 Minimum charge before surcharges
- [ ] 4.4 Fuel + security surcharges as separate ChargeLines
- [ ] 4.5 Pickup leg added exactly once (zone -> airport)
- [ ] 4.6 `candidates.py` merge tariff + portal -> eligible RateCandidates

## 5. Verification (independent fixtures)
- [ ] 5.1 Hand-checked fixtures: unit conversion, weight-break boundaries, minimums, FX, rounding, pickup
- [ ] 5.2 Determinism: same inputs + version reproduce same price
- [ ] 5.3 Missing required charge/currency/validity/rule blocks "complete"
- [ ] 5.4 Every tariff set -> approved records OR a visible review exception (no silent skip)

## 6. Handoff
- [ ] 6.1 Export RateCandidate records for a validated RFQ fixture to T3
- [ ] 6.2 Short README: how to run ingest, refresh portal, price an RFQ
