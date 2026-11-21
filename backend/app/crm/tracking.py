from datetime import UTC, datetime

from app.db.models import SessionRow


def reply(session: SessionRow) -> dict:
    return {
        "status": session.stage,
        "as_of": datetime.now(UTC).isoformat(),
        "simulated": bool(session.simulated),
    }
