import secrets
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.contracts.models import SessionEvent
from app.core.config import get_settings
from app.core.errors import DomainError, NotFoundError
from app.crm import queries
from app.crm.queries import SessionDetail, SessionListItem
from app.db.models import ActionIntentRow, GatewayUsageRow, MessageRow, SessionRow
from app.db.session import get_db, get_session_factory
from app.platform import get_adapters
from app.platform.outbox import deliver_intent
from app.workflows import followup

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


class DraftEdit(BaseModel):
    subject: str | None = None
    body: str


class MailResolution(BaseModel):
    approve: bool = False


class Resolution(BaseModel):
    sent: bool


def _session(db: Session, session_id: UUID) -> SessionRow:
    session = db.get(SessionRow, session_id)
    if session is None:
        raise NotFoundError(f"session {session_id}")
    db.refresh(session)  # another request may have paused or resumed it
    return session


def _action(intent: ActionIntentRow) -> dict:
    payload = intent.payload or {}
    return {
        "id": str(intent.id),
        "action_key": intent.action_key,
        "kind": intent.kind,
        "purpose": payload.get("purpose"),
        "delivery_status": intent.delivery_status,
        "to": payload.get("to"),
        "subject": payload.get("subject"),
        "body": payload.get("body"),
        "edited_by_operator": bool(payload.get("_edited")),
        "simulated": intent.simulated,
    }


def _owned_action(db: Session, session_id: UUID, action_id: UUID) -> ActionIntentRow:
    """The action, but only if a person has taken the session over (it is paused)."""
    session = _session(db, session_id)
    intent = db.get(ActionIntentRow, action_id)
    if intent is None or intent.rfq_id != session.rfq_id:
        raise NotFoundError(f"action {action_id}")
    if not session.paused:
        raise DomainError("take_over_first", "pause the session before changing its actions")
    return intent


@router.get("/api/sessions/{session_id}/actions")
def list_actions(session_id: UUID, db: Session = Depends(get_db)):
    """Every outbound action of the session with its real delivery state."""
    session = _session(db, session_id)
    intents = db.scalars(
        select(ActionIntentRow)
        .where(ActionIntentRow.rfq_id == session.rfq_id)
        .order_by(ActionIntentRow.created_at, ActionIntentRow.action_key)
    ).all()
    return [_action(i) for i in intents]


@router.put("/api/sessions/{session_id}/actions/{action_id}", dependencies=[Depends(operator)])
def edit_draft(session_id: UUID, action_id: UUID, body: DraftEdit, db: Session = Depends(get_db)):
    intent = _owned_action(db, session_id, action_id)
    if intent.delivery_status not in {"pending", "failed"}:
        raise DomainError("not_a_draft", f"action is {intent.delivery_status}")
    payload = dict(intent.payload or {})
    payload.update(body=body.body, subject=body.subject or payload.get("subject"), _edited=True)
    intent.payload = payload
    flag_modified(intent, "payload")
    db.flush()
    return _action(intent)


@router.post(
    "/api/sessions/{session_id}/actions/{action_id}/send", dependencies=[Depends(operator)]
)
def send_action(session_id: UUID, action_id: UUID, db: Session = Depends(get_db)):
    """A person approves and sends a draft, through the same outbox as the agents."""
    intent = _owned_action(db, session_id, action_id)
    if intent.delivery_status == "failed":
        intent.delivery_status = "pending"
    deliver_intent(db, intent, get_adapters()["mailbox"], operator=True)
    return _action(intent)


@router.post(
    "/api/sessions/{session_id}/actions/{action_id}/resolve", dependencies=[Depends(operator)]
)
def resolve_unknown(
    session_id: UUID, action_id: UUID, body: Resolution, db: Session = Depends(get_db)
):
    """An UNKNOWN send has no automatic answer. A person who checked says which it was."""
    intent = _owned_action(db, session_id, action_id)
    if intent.delivery_status != "UNKNOWN":
        raise DomainError("not_unknown", f"action is {intent.delivery_status}")
    intent.delivery_status = "confirmed" if body.sent else "pending"
    payload = dict(intent.payload or {})
    payload["_resolved_by_operator"] = "sent" if body.sent else "not_sent"
    intent.payload = payload
    flag_modified(intent, "payload")
    db.flush()
    return _action(intent)


@router.post("/api/review/mail/{external_id}/resolve", dependencies=[Depends(operator)])
def resolve_mail(
    external_id: str, body: MailResolution | None = None, db: Session = Depends(get_db)
):
    """A person has dealt with this mail: clear its flag. `approve` uses a carrier's bid that
    waited for review; clearing a tariff hold reprices that carrier's open offers."""
    message = db.scalars(select(MessageRow).where(MessageRow.external_id == external_id)).first()
    if message is None:
        raise NotFoundError(f"mail {external_id}")
    applied = followup.apply_review(db, message, approve=bool(body and body.approve))
    return {"external_id": external_id, "resolved": True, **applied}


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
            | (MessageRow.raw["bid"]["status"].astext == "review_required")
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
                "bid": (m.raw or {}).get("bid"),
            }
            for m in mails[-200:]
        ],
    }
