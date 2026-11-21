"""RFQ fields and dangerous-goods signals from customer mail.

Rules first: they are free and deterministic. A model may only fill fields the rules could not
find, and its answer is validated like any other untrusted input.
"""

import csv
import json
import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from app.pricing.pickup import airport_for_zone
from app.workflows import documents

REQUIRED = ("origin", "destination", "weight_kg")
_APP = Path(__file__).resolve().parents[1]

_CODE = re.compile(r"\b[A-Z]{3}\b")
_FROM_TO = re.compile(r"\bfrom\b[^.\n]*?\b([A-Z]{3})\b[^.\n]*?\bto\b[^.\n]*?\b([A-Z]{3})\b")
_PAIR = re.compile(r"\b([A-Z]{3})\s*(?:-|–|->|→|to)\s*([A-Z]{3})\b")
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
_WEIGHT = re.compile(
    _NUM + r"\s*(kg|kgs|kilos?|kilograms?|tons?|tonnes?|lbs?|pounds?)\b", re.IGNORECASE
)
_VOLUME = re.compile(_NUM + r"\s*(?:cbm|m3|m³|cubic met(?:er|re)s?)", re.IGNORECASE)
_PICKUP = re.compile(r"\b(?:pick[- ]?up|collect(?:ion)?|door)\b", re.IGNORECASE)
_ZONE = re.compile(r"\bzone\s*(\d{2})\b", re.IGNORECASE)
# Live customers name their site by German postcode; its first two digits are the pickup zone.
_POSTCODE = re.compile(r"\b(\d{5})\b(?!\s*(?:kgs?|cbm|m3|pieces|pcs)\b)", re.IGNORECASE)
# "we deliver to FRA ourselves", "no pickup needed", "no need to arrange pickup"
_SELF_DELIVERY = re.compile(
    r"\bdeliver\b[^.\n]*\bourselves\b"
    r"|\bno(?:\s+need\s+(?:to\s+arrange|for))?\s+(?:a\s+)?(?:pick[- ]?up|collection)\b",
    re.IGNORECASE,
)
_FASTEST = re.compile(
    r"\b(?:fastest|urgent(?:ly)?|asap|express|quickest|as soon as possible)\b", re.IGNORECASE
)

_NEGATED = re.compile(
    r"\bnon[- ]?(?:dangerous|hazardous|dg)(?:\s+goods)?\b"
    r"|\bnot\s+(?:classified\s+as\s+|a\s+)?(?:dangerous|hazardous)(?:\s+goods)?\b"
    r"|\bno\s+(?:dangerous|hazardous)\s+goods\b",
    re.IGNORECASE,
)
_SIGNAL = re.compile(
    r"\bUN\s?\d{4}\b|\blithium[- ](?:ion\s+|metal\s+)?batter\w+|\bclass\s?[1-9](?:\.\d)?\b"
    r"|\bhazmat\b|\bflammable\b|\bcorrosive\b|\bdangerous\s+goods\b(?!\s+declaration)|\bDGR?\b",
    re.IGNORECASE,
)


@lru_cache
def airports() -> tuple[frozenset[str], frozenset[str]]:
    """(airports we pick up for, every airport a listed airline serves)."""
    with (_APP / "pricing" / "data" / "pickup_zones.csv").open(encoding="utf-8") as handle:
        origins = {row["airport"] for row in csv.DictReader(handle)}
    with (_APP / "platform" / "data" / "carrier-list.csv").open(encoding="utf-8") as handle:
        served = {
            code
            for row in csv.DictReader(handle)
            if row["kind"] == "airline"
            for code in row["serves"].split()
        }
    return frozenset(origins), frozenset(served | origins)


@lru_cache
def _listed(kind: str, place: str) -> tuple[tuple[str, str], ...]:
    with (_APP / "platform" / "data" / "carrier-list.csv").open(encoding="utf-8") as handle:
        return tuple(
            (row["name"], row["email"].strip().lower())
            for row in csv.DictReader(handle)
            if row["kind"] == kind and place in row["serves"].split()
        )


def truckers_for_zone(zone: str) -> tuple[tuple[str, str], ...]:
    """(name, email) of every listed trucker that serves this pickup zone."""
    return _listed("trucker", str(zone))


def pickup_postcode(text: str, zone: str | None) -> str | None:
    """The full postcode the customer named for this pickup zone; truckers price by it."""
    return next((code for code in _POSTCODE.findall(text) if code[:2] == str(zone)), None)


def airlines_for(destination: str) -> tuple[tuple[str, str], ...]:
    """(name, email) of every listed airline that serves this airport."""
    return _listed("airline", str(destination))


def _decimal(raw: str) -> Decimal | None:
    if re.fullmatch(r"\d{1,3}\.\d{3}", raw):
        return None  # 1.200 is 1200 in one notation and 1.2 in another: ask, do not guess
    try:
        thousands = re.search(r",\d{3}", raw)
        value = Decimal(raw.replace(",", "") if thousands else raw.replace(",", "."))
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _route(text: str) -> tuple[str | None, str | None]:
    origins, known = airports()
    for pattern in (_FROM_TO, _PAIR):
        match = pattern.search(text)
        if match and match.group(1) in known and match.group(2) in known:
            return match.group(1), match.group(2)
    codes = [code for code in _CODE.findall(text) if code in known]
    origin = next((code for code in codes if code in origins), None)
    destination = next((code for code in codes if code != origin and code not in origins), None)
    return origin, destination or next((code for code in codes if code != origin), None)


def extract(subject: str, body: str) -> dict:
    """Fields one mail states, with notes about anything it states unusably.

    The body outranks the subject line: a live request arrived under the subject of an old
    forwarded mail ("RE: FW: ... RUH / 30 PCS 708 KGS") while asking for another shipment.
    """
    found = _read(body or "")
    for name, value in _read(subject or "")["fields"].items():
        found["fields"].setdefault(name, value)
    return found


def _read(text: str) -> dict:
    fields: dict = {}
    notes: list[str] = []
    origin, destination = _route(text)
    if origin:
        fields["origin"] = origin
    if destination:
        fields["destination"] = destination
    weight = _WEIGHT.search(text)
    if weight:
        value, unit = _decimal(weight.group(1)), weight.group(2).lower()
        if value is None:
            notes.append(f"weight '{weight.group(0)}' is ambiguous")
        elif unit.startswith(("lb", "pound")):
            notes.append(f"weight '{weight.group(0)}' is not in kg")
        else:
            fields["weight_kg"] = str(value * 1000 if unit.startswith("ton") else value)
    volume = _VOLUME.search(text)
    if volume and _decimal(volume.group(1)) is not None:
        fields["volume_m3"] = str(_decimal(volume.group(1)))
    zone = _ZONE.search(text)
    self_delivery = bool(_SELF_DELIVERY.search(text))
    postcode_zones = {code[:2] for code in _POSTCODE.findall(text)}
    if zone:
        fields["pickup_zone"] = zone.group(1)
    elif not self_delivery and len(postcode_zones) == 1:
        fields["pickup_zone"] = postcode_zones.pop()  # two different postcodes: ask, do not guess
    if zone or (not self_delivery and ("pickup_zone" in fields or _PICKUP.search(text))):
        fields["pickup_required"] = True
    if "origin" not in fields and airport_for_zone(fields.get("pickup_zone")):
        fields["origin"] = airport_for_zone(fields["pickup_zone"])  # the zone's departure airport
    if _FASTEST.search(text):
        fields["service_intent"] = "fastest"
    return {"fields": fields, "notes": notes}


def merge(mails: list[tuple[str, str]]) -> dict:
    """Combine the customer's mails, oldest first; a later mail adds or corrects a field."""
    fields: dict = {}
    notes: list[str] = []
    for subject, body in mails:
        found = extract(subject, body)
        fields.update(found["fields"])
        notes = found["notes"] or notes
    missing = [name for name in REQUIRED if name not in fields]
    return {"fields": fields, "notes": notes if "weight_kg" in missing else [], "missing": missing}


def model_fill(gateway, model: str, text: str, missing: list[str]) -> dict:
    """Ask a model for the missing fields only. Returns validated values, or {} on any doubt."""
    _, known = airports()
    prompt = (
        "Extract fields from this air-freight request. Answer with JSON only, using null for "
        "anything the mail does not state: "
        '{"origin": "IATA code", "destination": "IATA code", "weight_kg": number}\n\n' + text
    )
    for _ in range(2):  # one bounded retry
        try:
            data = json.loads(gateway.complete(prompt, model).get("text") or "")
        except Exception:  # noqa: BLE001 - timeout, HTTP error or invalid JSON: all mean "no answer"
            continue
        if not isinstance(data, dict):
            continue
        found: dict = {}
        for name in ("origin", "destination"):
            value = str(data.get(name) or "").strip().upper()
            if name in missing and value in known:
                found[name] = value
        if "weight_kg" in missing and data.get("weight_kg") is not None:
            weight = _decimal(str(data["weight_kg"]))
            if weight is not None and weight < 100000:
                found["weight_kg"] = str(weight)
        return found
    return {}


def model_check(gateway, model: str, text: str, fields: dict) -> list[str] | None:
    """A model reads the request on its own; returns the fields where it differs from the
    rules' reading (empty: it agrees), or None when it gave no usable answer."""
    prompt = (
        "Read this air-freight request. Answer with JSON only: "
        '{"destination": "IATA code", "weight_kg": total gross weight of the whole shipment, '
        '"volume_cbm": total volume or null, "pickup_postcode": "the 5-digit postcode a truck '
        'must collect from, or null if the customer delivers to the airport", '
        '"service": "cheapest" or "fastest"}\n\n' + text[:4000]
    )
    try:
        answer = gateway.complete(
            prompt, model, max_tokens=120, temperature=0, response_format={"type": "json_object"}
        )
        data = json.loads(answer.get("text") or "")
        postcode = str(data.get("pickup_postcode") or "")
        theirs = {
            "destination": str(data.get("destination") or "").upper(),
            "weight_kg": Decimal(str(data.get("weight_kg"))),
            "volume_m3": Decimal(str(data["volume_cbm"])) if data.get("volume_cbm") else None,
            "pickup_zone": postcode[:2] if re.fullmatch(r"\d{5}", postcode) else None,
            "service_intent": str(data.get("service") or "cheapest").lower(),
        }
    except Exception:  # noqa: BLE001 - no budget, timeout, HTTP error, bad JSON: no second opinion
        return None
    ours = {
        "destination": fields.get("destination"),
        "weight_kg": Decimal(str(fields.get("weight_kg") or 0)),
        "volume_m3": Decimal(str(fields["volume_m3"])) if fields.get("volume_m3") else None,
        "pickup_zone": fields.get("pickup_zone") if fields.get("pickup_required") else None,
        "service_intent": str(fields.get("service_intent") or "cheapest"),
    }
    return [name for name in ours if ours[name] != theirs[name]]


def assess_dg(mails: list[tuple[str, list[dict]]]) -> dict:
    """Dangerous-goods position from the customer's mails, oldest first.

    Each mail is (text, document evidence from `documents.collect`). A document counts only
    for what its content says; its file name is never read.
    status: none | docs_missing | conflict | documented | resolved_non_dg
    """
    signals: list[str] = []
    negated_first = resolved = affirmed = False
    for index, (text, _) in enumerate(mails):
        negated = bool(_NEGATED.search(text))
        own = [m.group(0) for m in _SIGNAL.finditer(_NEGATED.sub(" ", text))]
        signals += [s for s in own if s not in signals]
        if index == 0:
            negated_first = negated
        elif negated and not own:
            resolved = True  # asked about the conflict, the customer restated non-dangerous
        elif own and not negated:
            affirmed = True  # asked about the conflict, the customer confirmed dangerous goods
    request_un = documents.un_numbers(" ".join(signals))
    docs: set[str] = set()
    unreadable: list[str] = []
    rejected: list[dict] = []
    declared_by_document: list[str] = []
    evidence = [item for _, attachments in mails for item in attachments or []]
    for item in evidence:
        name, named = str(item.get("filename") or ""), item.get("un_numbers") or []
        kinds = item.get("kinds") or []
        if not item.get("readable"):
            unreadable.append(name)
        elif not kinds:
            reason = "it is neither a dangerous goods declaration nor a safety data sheet"
            rejected.append({"filename": name, "problem": "unrelated", "reason": reason})
        elif "dgd" in kinds and not named:
            reason = "the declaration names no UN number"
            rejected.append({"filename": name, "problem": "incomplete", "reason": reason})
        elif request_un and named and not set(request_un) & set(named):
            reason = f"it is for {', '.join(named)}, your request names {', '.join(request_un)}"
            rejected.append({"filename": name, "problem": "other_goods", "reason": reason})
        elif item.get("says_not_regulated") and "dgd" not in kinds:
            reason = "it says the goods are not regulated for transport"
            rejected.append({"filename": name, "problem": "contradicts_email", "reason": reason})
        else:
            docs.update(kinds)
            if "dgd" in kinds:
                declared_by_document += [un for un in named if un not in declared_by_document]
    # A declaration is itself a statement that the cargo is dangerous, whatever the mail says.
    signals += [un for un in declared_by_document if un not in signals]
    required = documents.required_documents(documents.un_numbers(" ".join(signals)))
    missing = [kind for kind in required if kind not in docs]
    contradicted = any(r["problem"] == "contradicts_email" for r in rejected)
    if not signals:
        status = "none"
    elif contradicted:
        status = "conflict"
    elif negated_first and not affirmed:
        status = "resolved_non_dg" if resolved and not declared_by_document else "conflict"
    elif not missing:
        status = "documented"
    else:
        status = "docs_missing"
    rules = documents.requirements()
    return {
        "status": status,
        "blocked": status in {"docs_missing", "conflict"},
        "signals": signals,
        "declared_non_dangerous": negated_first,
        "docs_present": sorted(docs),
        "docs_missing": missing if signals else [],
        "unreadable_attachments": unreadable,
        "rejected_documents": rejected,
        "documents": evidence,
        "requirements": {
            "documents": required,
            "confirmed": bool(rules.get("confirmed")),
            "source": rules.get("source"),
        },
    }
