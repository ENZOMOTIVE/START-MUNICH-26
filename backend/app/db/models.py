import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class MailboxCursorRow(Base):
    __tablename__ = "mailbox_cursors"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    since_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class MessageRow(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    external_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(320), nullable=False)
    recipient: Mapped[str] = mapped_column(String(320), default="", nullable=False)
    subject: Mapped[str] = mapped_column(Text, default="", nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="mailbox", nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class JobRow(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "run_after"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=5, nullable=False)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RfqRow(Base):
    __tablename__ = "rfqs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id"), unique=True, nullable=False
    )
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    destination: Mapped[str] = mapped_column(String(8), nullable=False)
    weight_kg: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    volume_m3: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pickup_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pickup_zone: Mapped[str | None] = mapped_column(String(8))
    service_intent: Mapped[str] = mapped_column(String(16), default="cheapest", nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(256))
    source_version: Mapped[str | None] = mapped_column(String(64))


class RateCandidateRow(Base):
    __tablename__ = "rate_candidates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=False, index=True
    )
    carrier: Mapped[str] = mapped_column(String(128), nullable=False)
    carrier_code: Mapped[str | None] = mapped_column(String(8))
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    transit_days: Mapped[int | None] = mapped_column()
    rate_id: Mapped[str | None] = mapped_column(String(64))
    complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    charges: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class QuoteDecisionRow(Base):
    __tablename__ = "quote_decisions"
    __table_args__ = (UniqueConstraint("rfq_id", name="uq_quote_decision_rfq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=False
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rate_candidates.id"), nullable=False
    )
    total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)


class ActionIntentRow(Base):
    __tablename__ = "action_intents"
    __table_args__ = (
        UniqueConstraint("action_key", name="uq_action_key"),
        Index(
            "uq_first_quote_per_rfq",
            "rfq_id",
            unique=True,
            postgresql_where=text("kind = 'first_quote'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=False, index=True
    )
    action_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    delivery_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BookingRow(Base):
    __tablename__ = "bookings"
    __table_args__ = (UniqueConstraint("team_reference", name="uq_booking_team_reference"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=False, index=True
    )
    quote_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("quote_decisions.id"), nullable=False
    )
    team_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    rate_id: Mapped[str | None] = mapped_column(String(64))
    booking_reference: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(8))
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class SessionRow(Base):
    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("rfq_id", name="uq_session_rfq"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rfq_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rfqs.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(16), default="intake", nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    exception: Mapped[str | None] = mapped_column(String(128))
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SessionEventRow(Base):
    __tablename__ = "session_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class GatewayUsageRow(Base):
    __tablename__ = "gateway_usage"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    cost_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    observed_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
