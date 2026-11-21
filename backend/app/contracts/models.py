from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

MoneyStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: format(v.quantize(Decimal("0.01")), "f"), return_type=str),
]
QtyStr = Annotated[
    Decimal,
    PlainSerializer(lambda v: format(v, "f"), return_type=str),
]


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "UNKNOWN"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    QUOTED = "quoted"
    ACCEPTED = "accepted"
    BOOKED = "booked"
    EXPIRED = "expired"
    FAILED = "failed"


class SessionStage(StrEnum):
    INTAKE = "intake"
    PRICING = "pricing"
    QUOTED = "quoted"
    ACCEPTED = "accepted"
    BOOKED = "booked"
    EXCEPTION = "exception"


class ServiceIntent(StrEnum):
    CHEAPEST = "cheapest"
    FASTEST = "fastest"


class ContractModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ChargeLine(ContractModel):
    code: str
    amount: MoneyStr
    currency: str
    unit: str = "shipment"


class Message(ContractModel):
    id: UUID
    external_id: str
    thread_id: str
    sender: str
    recipient: str = ""
    subject: str
    body: str
    received_at: datetime
    classification: str = "unknown"
    source: str = "mailbox"
    simulated: bool = False
    raw: dict[str, Any] = Field(default_factory=dict)


class RFQ(ContractModel):
    id: UUID
    message_id: UUID
    thread_id: str
    origin: str
    destination: str
    weight_kg: QtyStr
    volume_m3: QtyStr | None = None
    weight_unit: str = "kg"
    volume_unit: str = "m3"
    pickup_required: bool = False
    pickup_zone: str | None = None
    service_intent: ServiceIntent = ServiceIntent.CHEAPEST
    received_at: datetime
    expires_at: datetime
    status: WorkflowStatus = WorkflowStatus.PENDING
    source_ref: str | None = None
    source_version: str | None = None


class RateCandidate(ContractModel):
    id: UUID
    rfq_id: UUID
    carrier: str
    carrier_code: str | None = None
    total: MoneyStr
    currency: str
    transit_days: int | None = None
    rate_id: str | None = None
    complete: bool = False
    valid_until: datetime | None = None
    source_ref: str
    source_version: str
    charges: list[ChargeLine] = Field(default_factory=list)
    simulated: bool = True


class QuoteDecision(ContractModel):
    id: UUID
    rfq_id: UUID
    candidate_id: UUID
    total: MoneyStr
    currency: str
    decided_at: datetime
    evidence: dict[str, Any] = Field(default_factory=dict)


class ActionIntent(ContractModel):
    id: UUID
    rfq_id: UUID
    action_key: str
    kind: Literal["first_quote", "booking", "tracking", "reply"]
    payload: dict[str, Any] = Field(default_factory=dict)
    workflow_status: WorkflowStatus = WorkflowStatus.PENDING
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING
    retryable: bool = False
    simulated: bool = True
    created_at: datetime


class Booking(ContractModel):
    id: UUID
    rfq_id: UUID
    quote_id: UUID
    team_reference: str
    rate_id: str | None = None
    booking_reference: str | None = None
    status: Literal["pending", "submitted", "confirmed", "failed", "UNKNOWN"] = "pending"
    total: MoneyStr | None = None
    currency: str | None = None
    simulated: bool = True


class Session(ContractModel):
    id: UUID
    rfq_id: UUID
    stage: SessionStage
    deadline: datetime
    paused: bool = False
    exception: str | None = None
    simulated: bool = True


class SessionEvent(ContractModel):
    id: UUID
    session_id: UUID
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    simulated: bool = True
