from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.contracts.models import SessionEvent
from app.crm import queries
from app.crm.queries import SessionDetail, SessionListItem
from app.db.session import get_db

router = APIRouter()


@router.get("/api/sessions", response_model=list[SessionListItem])
def list_sessions(db: Session = Depends(get_db)):
    return queries.session_list(db)


@router.get("/api/sessions/{session_id}", response_model=SessionDetail)
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    return queries.session_detail(db, session_id)


@router.get("/api/sessions/{session_id}/events", response_model=list[SessionEvent])
def get_session_events(session_id: UUID, db: Session = Depends(get_db)):
    return queries.session_detail(db, session_id).events


@router.post("/api/sessions/{session_id}/pause", response_model=SessionDetail)
def pause(session_id: UUID, db: Session = Depends(get_db)):
    session = queries.pause_session(db, session_id)
    return queries.session_detail(db, session.id)


@router.post("/api/sessions/{session_id}/resume", response_model=SessionDetail)
def resume(session_id: UUID, db: Session = Depends(get_db)):
    session = queries.resume_session(db, session_id)
    return queries.session_detail(db, session.id)
