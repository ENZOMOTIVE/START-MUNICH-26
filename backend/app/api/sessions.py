from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.crm import queries
from app.db.session import get_db

router = APIRouter()


@router.get("/api/sessions")
def list_sessions(db: Session = Depends(get_db)):
    return queries.session_list(db)


@router.get("/api/sessions/{session_id}")
def get_session(session_id: UUID, db: Session = Depends(get_db)):
    return queries.session_detail(db, session_id)


@router.get("/api/sessions/{session_id}/events")
def get_session_events(session_id: UUID, db: Session = Depends(get_db)):
    detail = queries.session_detail(db, session_id)
    return detail.events


@router.post("/api/sessions/{session_id}/pause")
def pause(session_id: UUID, db: Session = Depends(get_db)):
    session = queries.pause_session(db, session_id)
    return queries.session_detail(db, session.id)


@router.post("/api/sessions/{session_id}/resume")
def resume(session_id: UUID, db: Session = Depends(get_db)):
    session = queries.resume_session(db, session_id)
    return queries.session_detail(db, session.id)
