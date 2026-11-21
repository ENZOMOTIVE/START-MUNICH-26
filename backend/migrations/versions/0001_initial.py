"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mailbox_cursors",
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("since_id", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.String(128), nullable=False, unique=True),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("sender", sa.String(320), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("source", sa.String(32), nullable=False, server_default="mailbox"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("raw", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"])
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("claimed_by", sa.String(64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "run_after",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after"])
    op.create_table(
        "rfqs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("messages.id"), nullable=False, unique=True),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("origin", sa.String(8), nullable=False),
        sa.Column("destination", sa.String(8), nullable=False),
        sa.Column("weight_kg", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume_m3", sa.Numeric(18, 4)),
        sa.Column("pickup_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pickup_zone", sa.String(8)),
        sa.Column("service_intent", sa.String(16), nullable=False, server_default="cheapest"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("source_ref", sa.String(256)),
        sa.Column("source_version", sa.String(64)),
    )
    op.create_index("ix_rfqs_thread_id", "rfqs", ["thread_id"])
    op.create_table(
        "rate_candidates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rfqs.id"), nullable=False),
        sa.Column("carrier", sa.String(128), nullable=False),
        sa.Column("carrier_code", sa.String(8)),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("transit_days", sa.Integer()),
        sa.Column("rate_id", sa.String(64)),
        sa.Column("complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("source_version", sa.String(64), nullable=False),
        sa.Column("charges", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_rate_candidates_rfq_id", "rate_candidates", ["rfq_id"])
    op.create_table(
        "quote_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rfqs.id"), nullable=False),
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rate_candidates.id"), nullable=False),
        sa.Column("total", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.UniqueConstraint("rfq_id", name="uq_quote_decision_rfq"),
    )
    op.create_table(
        "action_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rfqs.id"), nullable=False),
        sa.Column("action_key", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("workflow_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("delivery_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("retryable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("action_key", name="uq_action_key"),
    )
    op.create_index("ix_action_intents_rfq_id", "action_intents", ["rfq_id"])
    op.create_index(
        "uq_first_quote_per_rfq",
        "action_intents",
        ["rfq_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'first_quote'"),
    )
    op.create_table(
        "bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rfqs.id"), nullable=False),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("quote_decisions.id"), nullable=False),
        sa.Column("team_reference", sa.String(128), nullable=False),
        sa.Column("rate_id", sa.String(64)),
        sa.Column("booking_reference", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("total", sa.Numeric(18, 2)),
        sa.Column("currency", sa.String(8)),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("team_reference", name="uq_booking_team_reference"),
    )
    op.create_index("ix_bookings_rfq_id", "bookings", ["rfq_id"])
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("rfq_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("rfqs.id"), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False, server_default="intake"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("exception", sa.String(128)),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("rfq_id", name="uq_session_rfq"),
    )
    op.create_table(
        "session_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sessions.id"), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_session_events_session_id", "session_events", ["session_id"])
    op.create_table(
        "gateway_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("cost_kind", sa.String(16), nullable=False),
        sa.Column("observed_cost", sa.Numeric(18, 6)),
        sa.Column("estimated_cost", sa.Numeric(18, 6)),
        sa.Column("currency", sa.String(8), nullable=False, server_default="USD"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    raise RuntimeError("downgrade is disabled (no table drops)")
