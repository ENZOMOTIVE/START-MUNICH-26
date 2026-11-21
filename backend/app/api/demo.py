from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.workflows.demo import run_demo

router = APIRouter()


@router.post("/internal/demo/run")
def run_internal_demo(db: Session = Depends(get_db)):
    session_id = run_demo(db)
    return {"session_id": str(session_id), "simulated": True}
