import secrets
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contracts.models import SessionEvent
from app.core.config import get_settings
from app.crm import queries
from app.crm.queries import SessionDetail, SessionListItem
from app.db.models import GatewayUsageRow, MessageRow, SessionRow
from app.db.session import get_db, get_session_factory

router = APIRouter()


def operator(x_operator_token: str | None = Header(default=None)) -> None:
    """Commands that change what the agents do need the operator token, when one is set."""
    token = get_settings().operator_token
    if token and not secrets.compare_digest(x_operator_token or "", token):
        raise HTTPException(status_code=401, detail="operator token required")


@router.get("/api/sessions", response_model=list[SessionListItem])
def list_sessions(db: Session = Depends(get_db)):
    return queries.session_list(db)


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    return queries.session_detail(db, session_id)


@router.get("/api/sessions/{session_id}/events", response_model=list[SessionEvent])
def get_session_events(session_id: UUID, db: Session = Depends(get_db)):
    return queries.session_detail(db, session_id).events


@router.get("/api/sessions/{session_id}/stream")
def stream_session_events(
    session_id: UUID,
    follow: bool = True,
    after: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    """Server-sent events for one session's timeline.

    The event id is the event's position in the timeline, so a client that reconnects with
    Last-Event-ID (or `after`) gets exactly the events it has not seen, once.
    """
    queries.session_detail(db, session_id)  # 404 before the stream starts
    position = int(last_event_id) if (last_event_id or "").isdigit() else after

    def events():
        seen = position
        while True:
            with get_session_factory()() as fresh:
                timeline = queries.session_detail(fresh, session_id).events
            for index, event in enumerate(timeline[seen:], start=seen + 1):
                yield f"id: {index}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
            seen = max(seen, len(timeline))
            if not follow:
                return
            yield ": keep-alive\n\n"
            time.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream")


@router.post(
    "/api/sessions/{session_id}/pause",
    response_model=SessionDetail,
    dependencies=[Depends(operator)],
)
def pause(session_id: UUID, db: Session = Depends(get_db)):
    session = queries.pause_session(db, session_id)
    return queries.session_detail(db, session.id)


@router.post(
    "/api/sessions/{session_id}/resume",
    response_model=SessionDetail,
    dependencies=[Depends(operator)],
)
def resume(session_id: UUID, db: Session = Depends(get_db)):
    session = queries.resume_session(db, session_id)
    return queries.session_detail(db, session.id)


@router.get("/api/usage")
def usage(db: Session = Depends(get_db)):
    """Model gateway calls by model and cost kind. A cost is shown only where one was
    observed or estimated; `unknown` means the call was counted and its cost is not known."""
    rows = db.execute(
        select(
            GatewayUsageRow.model,
            GatewayUsageRow.cost_kind,
            GatewayUsageRow.simulated,
            func.count(),
            func.sum(GatewayUsageRow.observed_cost),
            func.sum(GatewayUsageRow.estimated_cost),
        ).group_by(GatewayUsageRow.model, GatewayUsageRow.cost_kind, GatewayUsageRow.simulated)
    ).all()
    return [
        {
            "model": model,
            "cost_kind": kind,
            "simulated": simulated,
            "calls": calls,
            "observed_cost": str(observed) if observed is not None else None,
            "estimated_cost": str(estimated) if estimated is not None else None,
        }
        for model, kind, simulated, calls, observed, estimated in rows
    ]


@router.get("/api/review")
def review(db: Session = Depends(get_db)):
    """Everything that waits for a person: blocked sessions, mail that matched no shipment,
    and tariff files that differ from the approved ones."""
    sessions = db.scalars(
        select(SessionRow).where(SessionRow.exception.is_not(None)).order_by(SessionRow.created_at)
    ).all()
    mails = db.scalars(
        select(MessageRow)
        .where(
            MessageRow.raw.has_key("unmatched")  # noqa: W601 - JSONB key test, not dict.has_key
            | (MessageRow.raw["tariff"]["status"].astext == "review_required")
        )
        .order_by(MessageRow.received_at)
    ).all()
    return {
        "sessions": [
            {"id": str(s.id), "stage": s.stage, "reason": s.exception, "paused": s.paused}
            for s in sessions[-200:]
        ],
        "mail": [
            {
                "external_id": m.external_id,
                "sender": m.sender,
                "subject": m.subject,
                "kind": m.classification,
                "unmatched": (m.raw or {}).get("unmatched"),
                "tariff": (m.raw or {}).get("tariff"),
            }
            for m in mails[-200:]
        ],
    }
