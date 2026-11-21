# pricing/ — T2 rate data & pricing

Deterministic, Decimal-only pricing. **No LLM / gateway call anywhere in this package** (cost points).

## Setup
```bash
pip install -r pricing/requirements.txt          # add --break-system-packages on Debian boxes
sudo apt-get install -y tesseract-ocr poppler-utils   # OCR for the Danube Wings JPGs; pdftotext (optional)
cp .env.example .env                              # put the real HACK_API_KEY here; .env is git-ignored
```
Without tesseract, the Danube Wings set falls back to the hand transcription in
`pricing/tariffs/manual/` (trusted only for the exact image checksums it was made from) and a visible
`ocr_unavailable` review exception is raised.

## 1. Ingest tariffs
```bash
python -m pricing.tariffs.ingest                  # starter-kit/ -> var/pricing_store/ + pricing/data/tariff_snapshot.json
python -m pricing.tariffs.ingest --kit path/to/new/attachments   # a newly mailed tariff -> new version
```
Prints one line per tariff set: `approved | partial | review`, approved lane count, blocking / non-blocking
review exceptions. Every file in the folder is listed (tariff, reference data, or "unrecognised").
Re-ingesting identical bytes is a no-op; changed bytes create a new version and older versions stay loadable
(`TariffStore.record(set_id, version, origin, dest)`), so a sent quote keeps its original evidence.

## 2. Portal (read-only)
```bash
python -m pricing.portal ports                    # lanes + airlines (not rate-limited)
python -m pricing.portal brackets JP              # weight breaks (404 => airline prices by email)
python -m pricing.portal rate CGN DEL JP 80 0.2   # one live rate -> RateCandidate JSON (costs 1 of 10 reads/min)
```
This module never POSTs. Booking by `rate_id` is T3's job.

## 3. Price an RFQ
```bash
python -m pricing.candidates tests/pricing/fixtures/rfq_cgn_del_80kg_pickup.json            # tariffs only
python -m pricing.candidates tests/pricing/fixtures/rfq_cgn_del_80kg_pickup.json --live --pickup-cost 45.00
```
From code (what T3 calls):
```python
from pricing.candidates import TariffBook, build_candidates
from pricing.engine import PriceRequest
from pricing.pickup import PickupQuote
from pricing.portal import PortalClient

book = TariffBook.from_snapshot()                       # or .from_store() / .from_kit()
req = PriceRequest(destination="DEL", gross_kg=Decimal("80"), volume_m3=Decimal("0.2"),
                   pickup_zone="50", prefer="cheapest")  # origin derived from zone (50 -> CGN)
async with PortalClient() as portal:
    cs = await build_candidates(req, book, portal, pickup=PickupQuote("50", "CGN", Decimal("45.00")))
best = cs.best()            # eligible (complete + unexpired), ranked by req.prefer
best.total, best.rate_id, best.valid_until, best.charges, best.to_dict()
```

## Rules implemented
* chargeable = `max(gross_kg, m3 x divisor)`; divisor read from each sheet ("1 cbm = 167 kg"), 167 default.
* bracket = highest break <= chargeable. **Pivot**: also price every higher break at its minimum weight; take
  the cheapest. Portal: probes the next break when it is within 1.6x of the chargeable weight.
* minimum charge applies to freight **before** surcharges; fuel + security per kg of **billed** weight, as
  separate `ChargeLine`s.
* rounding: ROUND_HALF_UP to cents per charge line, total = sum of lines (matches the live portal; see
  `engine.py` docstring). `rounding="total"` available.
* portal `total` already includes freight + fuel + security: never re-added. FX field = EUR per 1 unit of the
  returned currency. Pickup is always a separate line, added exactly once, only when the RFQ has a pickup zone.
* a truck price is never invented: a pickup RFQ without a `PickupQuote` gives `complete=False, missing=["pickup"]`.
* expired portal rates (`valid_until` passed; 30 s safety margin) are never eligible/bookable.
* non-EUR tariff without an FX source -> incomplete. Unreadable/ambiguous sheet values -> `ReviewException`.

## Tests
```bash
python -m pytest tests/pricing -q
```
