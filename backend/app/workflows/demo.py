import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import DomainError
from app.platform.mailbox import ingest_message
from app.workflows.booking import handle_acceptance
from app.workflows.rfq import handle_rfq

_DEFAULT_RFQ = {
    "external_id": "demo-rfq",
    "thread_id": "demo-thread",
    "sender": "shipper@example.com",
    "recipient": "desk@example.com",
    "subject": "RFQ FRA-LAX",
    "body": "Please quote RFQ FRA to LAX 500kg",
    "classification": "rfq",
    "rfq": {
        "origin": "FRA",
        "destination": "LAX",
        "weight_kg": "500",
        "volume_m3": "2.5",
        "pickup_required": False,
        "pickup_zone": None,
        "service_intent": "cheapest",
    },
}

_DEFAULT_ACCEPTANCE = {
    "external_id": "demo-acceptance",
    "sender": "shipper@example.com",
    "recipient": "desk@example.com",
    "subject": "Re: RFQ FRA-LAX",
    "body": "we accept the quote",
    "classification": "acceptance",
}


def _load_json(path: Path, fallback: dict) -> dict:
    if path.is_file():
        return json.loads(path.read_text())
    return dict(fallback)


def run_demo(db: Session):
    if get_settings().app_mode != "demo":
        raise DomainError("demo_mode_only")

    stamp = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    root = get_settings().fixtures_path()
    rfq_data = _load_json(root / "messages" / "demo-rfq.json", _DEFAULT_RFQ)
    acc_path = root / "messages" / "demo-acceptance.json"
    acc_data = _load_json(acc_path, _DEFAULT_ACCEPTANCE)
    scenario_path = root / "scenarios" / "demo-happy-path.json"
    if scenario_path.is_file():
        json.loads(scenario_path.read_text())

    thread_id = f"{rfq_data.get('thread_id', 'demo-thread')}-{stamp}"
    raw = dict(rfq_data.get("raw") or {})
    if "rfq" not in raw:
        raw["rfq"] = rfq_data.get("rfq") or _DEFAULT_RFQ["rfq"]
    raw.setdefault("simulated", True)

    message, _ = ingest_message(
        db,
        {
            "external_id": f"{rfq_data.get('external_id', 'demo-rfq')}-{stamp}",
            "thread_id": thread_id,
            "sender": rfq_data.get("sender", "shipper@example.com"),
            "recipient": rfq_data.get("recipient", "desk@example.com"),
            "subject": rfq_data.get("subject", "RFQ FRA-LAX"),
            "body": rfq_data.get("body", "Please quote RFQ"),
            "received_at": now,
            "classification": "rfq",
            "source": "demo",
            "simulated": True,
            "raw": raw,
        },
    )
    session = handle_rfq(db, message)

    handle_acceptance(
        db,
        ingest_message(
            db,
            {
                "external_id": f"{acc_data.get('external_id', 'demo-acceptance')}-{stamp}",
                "thread_id": thread_id,
                "sender": acc_data.get("sender", "shipper@example.com"),
                "recipient": acc_data.get("recipient", "desk@example.com"),
                "subject": acc_data.get("subject", "Re: RFQ FRA-LAX"),
                "body": acc_data.get("body", "we accept the quote"),
                "received_at": now,
                "classification": "acceptance",
                "source": "demo",
                "simulated": True,
                "raw": {**(acc_data.get("raw") or {}), "simulated": True},
            },
        )[0],
    )
    return session.id
