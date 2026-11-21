# app/pricing/parsers — FOLLOW-UP / BONUS (not on the T2 acceptance path)

`docs/tasks/T2.md` scopes T2 as fixtures-first + portal calculator and says *"do not build a full parser in
this task"*. This package is that follow-up: it is **not imported by `make demo`, the demo workflow or the
T2 acceptance tests**, and its extra deps are an optional uv group.

What it does: ingests every starter-kit tariff (TXT, XLSX, PDF, DOCX, and the Danube Wings JPG scans via
multi-pass tesseract OCR cross-checked against `manual/`), validates each lane, and produces approved
`LaneRate`s (158 lanes / 10 sets on the 21 Nov kit, 0 blocking exceptions). Anything unreadable becomes a
visible `ReviewException`, never a guessed number. Output feeds `engine.tariff_candidate()` on the live path
through `../data/tariff_snapshot.json`. Versioned store: identical bytes are a no-op, changed bytes are a
new version, old versions stay loadable.

```sh
cd backend
uv sync --group parsers                           # openpyxl, pdfplumber, python-docx, pillow
sudo apt-get install -y tesseract-ocr poppler-utils
uv run python -m app.pricing.parsers.ingest       # starter-kit/ -> var/pricing_store + data/tariff_snapshot.json
uv run python -m app.pricing.parsers.ingest --kit path/to/new/attachments
uv run pytest -q tests/pricing/test_ingest.py     # skipped automatically without the parsers group
```

Other CLIs (live, read-only GETs):

```sh
uv run python -m app.pricing.portal_client ports | brackets JP | rate CGN DEL JP 80 0.2
uv run python -m app.pricing.tariff_candidates tests/pricing/fixtures/rfq_cgn_del_80kg_pickup.json [--live]
```
