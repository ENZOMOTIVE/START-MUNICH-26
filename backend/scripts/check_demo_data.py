"""Audit demo fixtures against the supplied starter-kit data.

This is deliberately a quality gate, not a tariff parser.  It checks the
fixture contract and catches unsupported claims before a demo is presented.
Run with ``python scripts/check_demo_data.py --strict`` to make findings fail
the command.
"""

import argparse
import csv
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
STARTER_KIT = ROOT / "starter-kit"
REQUIRED_SOURCE_FILES = {
    "carrier-list.csv",
    "pickup_zones.csv",
    "cobalt-skylift-tariff-2026-11.pdf",
    "danube-wings-cargo-tariff-2026-11-p1.jpg",
    "danube-wings-cargo-tariff-2026-11-p2.jpg",
    "elbflug-air-freight-tariff-2026-11.xlsx",
    "helix-air-freight-tariff-2026-11.pdf",
    "kestrel-airways-tariff-2026-11.xlsx",
    "orion-cargo-air-tariff-2026-11.pdf",
    "quetzal-air-freight-tariff-2026-11.docx",
    "vega-skyfreight-tariff-2026-11.xlsx",
    "wayzata-air-cargo-tariff-2026-11.xlsx",
    "yellowtail-air-freight-tariff-2026-11.txt",
}


def _money(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def audit() -> dict[str, list[str]]:
    """Return human-readable errors and warnings for the checked-in demo data."""
    errors: list[str] = []
    warnings: list[str] = []

    missing_sources = sorted(
        filename for filename in REQUIRED_SOURCE_FILES if not (STARTER_KIT / filename).is_file()
    )
    for filename in missing_sources:
        errors.append(f"missing starter-kit source file: {filename}")

    with (STARTER_KIT / "carrier-list.csv").open(newline="", encoding="utf-8") as source:
        carriers = {row["name"] for row in csv.DictReader(source)}
    with (STARTER_KIT / "pickup_zones.csv").open(newline="", encoding="utf-8") as source:
        pickup_zones = {row["zone"]: row["airport"] for row in csv.DictReader(source)}

    rfq_path = FIXTURES / "messages" / "demo-rfq.json"
    acceptance_path = FIXTURES / "messages" / "demo-acceptance.json"
    rates_path = FIXTURES / "rates" / "demo-fra-lax.json"
    scenario_path = FIXTURES / "scenarios" / "demo-happy-path.json"
    required_fixtures = (rfq_path, acceptance_path, rates_path, scenario_path)
    for path in required_fixtures:
        if not path.is_file():
            errors.append(f"missing required demo fixture: {path.relative_to(ROOT)}")
    if errors:
        return {"errors": errors, "warnings": warnings}

    rfq = json.loads(rfq_path.read_text(encoding="utf-8"))
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    rates = json.loads(rates_path.read_text(encoding="utf-8"))
    scenario = json.loads(scenario_path.read_text(encoding="utf-8"))

    if rfq.get("classification") != "rfq":
        errors.append("demo RFQ must have classification 'rfq'")
    rfq_data = rfq.get("rfq", {})
    if (rfq_data.get("origin"), rfq_data.get("destination")) != ("FRA", "LAX"):
        errors.append("demo RFQ must describe the FRA-LAX demo lane")
    if _money(rfq_data.get("weight_kg")) is None:
        errors.append("demo RFQ weight_kg must be a decimal-compatible value")
    pickup_zone = rfq_data.get("pickup_zone")
    if pickup_zone is not None and str(pickup_zone) not in pickup_zones:
        errors.append(f"demo RFQ references unknown pickup zone: {pickup_zone}")
    if acceptance.get("classification") != "acceptance":
        errors.append("demo acceptance must have classification 'acceptance'")
    if not scenario.get("simulated"):
        errors.append("demo scenario must be marked simulated")
    if not {"rfq", "quote", "acceptance", "booking"}.issubset(set(scenario.get("steps", []))):
        errors.append("demo scenario must include RFQ, quote, acceptance, and booking steps")

    candidates = rates.get("candidates")
    if not isinstance(candidates, list) or len(candidates) < 3:
        errors.append("demo rate fixture must contain at least three candidates")
        candidates = []
    cheapest: tuple[Decimal, str] | None = None
    for candidate in candidates:
        name = candidate.get("carrier", "<unnamed>")
        if name not in carriers:
            errors.append(f"fixture carrier is absent from carrier-list.csv: {name}")
        total = _money(candidate.get("total"))
        charges = candidate.get("charges")
        if total is None or total < 0:
            errors.append(f"{name}: total must be a non-negative decimal string")
            continue
        if not candidate.get("complete"):
            errors.append(f"{name}: demo candidate must be complete")
        if candidate.get("currency") != "EUR":
            errors.append(f"{name}: demo candidate currency must be EUR")
        if not isinstance(charges, list) or not charges:
            errors.append(f"{name}: charges must be a non-empty list")
        else:
            amounts = [_money(charge.get("amount")) for charge in charges]
            if any(amount is None for amount in amounts):
                errors.append(f"{name}: every charge amount must be decimal-compatible")
            elif sum(amounts, Decimal("0")) != total:
                errors.append(f"{name}: total does not equal the sum of charges")
        if cheapest is None or total < cheapest[0]:
            cheapest = (total, name)

    if cheapest is None:
        errors.append("demo rate fixture has no valid candidate total")
    elif cheapest[1] != "Yellowtail Air Freight":
        errors.append("Yellowtail Air Freight must be the cheapest demo candidate")

    # A small, intentional cross-check for the only text-native source tariff.
    # Parsing PDFs, spreadsheets, and images remains T2 work, not this T5 gate.
    yellowtail_text = (STARTER_KIT / "yellowtail-air-freight-tariff-2026-11.txt").read_text(
        encoding="utf-8"
    )
    if "FRA-LAX" not in yellowtail_text:
        errors.append(
            "Yellowtail fixture claims FRA-LAX, but its supplied tariff has no FRA-LAX lane; "
            "T2 must correct the fixture or document an approved non-tariff source."
        )

    if "source_ref" not in rates or "source_version" not in rates:
        warnings.append("rate fixture should identify source_ref and source_version")
    return {"errors": errors, "warnings": warnings}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit demo fixtures against starter-kit inputs.")
    parser.add_argument(
        "--strict", action="store_true", help="return a non-zero exit code on errors"
    )
    args = parser.parse_args()
    report = audit()
    print(json.dumps(report, indent=2))
    return 1 if args.strict and report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
