from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.contracts.models import RFQ, Booking, QuoteDecision, RateCandidate, SessionEvent
from app.core.errors import NotFoundError
from app.db import repositories
from app.db.models import RateCandidateRow, RfqRow, SessionRow


class SessionListItem(BaseModel):
    id: UUID
    origin: str | None = None
    destination: str | None = None
    deadline: datetime
    stage: str
    paused: bool
    exception: str | None = None
    simulated: bool = True
    rfq: dict | None = None


class SessionDetail(BaseModel):
    id: UUID
    rfq_id: UUID
    stage: str
    paused: bool
    exception: str | None = None
    deadline: datetime
    simulated: bool = True
    request: RFQ | None = None
    rfq: RFQ | None = None
    selected_offer: RateCandidate | None = None
    evidence: dict | None = None
    quote: QuoteDecision | None = None
    booking: Booking | None = None
    events: list[SessionEvent] = []


def _as_uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def session_detail(db: Session, session_id: UUID | str) -> SessionDetail:
    session_id = _as_uuid(session_id)
    session = db.get(SessionRow, session_id)
    if session is None:
        raise NotFoundError(f"session {session_id}")
    rfq = db.get(RfqRow, session.rfq_id)
    quote = repositories.quote_for(db, session.rfq_id)
    booking = repositories.booking_for(db, session.rfq_id)
    candidate = db.get(RateCandidateRow, quote.candidate_id) if quote is not None else None
    events = repositories.events_for(db, session.id)
    return SessionDetail(
        id=session.id,
        rfq_id=session.rfq_id,
        stage=session.stage,
        paused=session.paused,
        exception=session.exception,
        deadline=session.deadline,
        simulated=session.simulated,
        request=RFQ.model_validate(rfq) if rfq is not None else None,
        rfq=RFQ.model_validate(rfq) if rfq is not None else None,
        selected_offer=RateCandidate.model_validate(candidate) if candidate is not None else None,
        evidence=quote.evidence if quote is not None else None,
        quote=QuoteDecision.model_validate(quote) if quote is not None else None,
        booking=Booking.model_validate(booking) if booking is not None else None,
        events=[SessionEvent.model_validate(event) for event in events],
    )


def session_list(db: Session) -> list[SessionListItem]:
    items: list[SessionListItem] = []
    for session in repositories.list_sessions(db):
        rfq = db.get(RfqRow, session.rfq_id)
        items.append(
            SessionListItem(
                id=session.id,
                origin=rfq.origin if rfq is not None else None,
                destination=rfq.destination if rfq is not None else None,
                deadline=session.deadline,
                stage=session.stage,
                paused=session.paused,
                exception=session.exception,
                simulated=session.simulated,
                rfq=(
                    {
                        "origin": rfq.origin,
                        "destination": rfq.destination,
                        "weight_kg": str(rfq.weight_kg),
                    }
                    if rfq is not None
                    else None
                ),
            )
        )
    return items


def pause_session(db: Session, session_id: UUID | str) -> SessionRow:
    session = db.get(SessionRow, _as_uuid(session_id))
    if session is None:
        raise NotFoundError(f"session {session_id}")
    session.paused = True
    repositories.append_event(db, session.id, "paused", {"simulated": True})
    db.flush()
    return session


def resume_session(db: Session, session_id: UUID | str) -> SessionRow:
    session = db.get(SessionRow, _as_uuid(session_id))
    if session is None:
        raise NotFoundError(f"session {session_id}")
    session.paused = False
    repositories.append_event(db, session.id, "resumed", {"simulated": True})
    db.flush()
    return session
