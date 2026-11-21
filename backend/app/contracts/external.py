"""Shapes taken from https://api.start-hack.5u.ai/openapi.json (2026-11-21).

Do not invent extra live endpoints. Implement live calls only after re-checking
that contract. Poll responses are additionalProperties in the spec; we persist
the raw payload and only read documented fields.
"""

from typing import Any

from pydantic import BaseModel, Field


class HackathonMessageIn(BaseModel):
    to: str
    subject: str = ""
    body: str = ""
    in_reply_to: int | None = None
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class HackathonBookingIn(BaseModel):
    rate_id: str
    team_reference: str


class HackathonBookingOut(BaseModel):
    booking_reference: str
    team_reference: str
    rate_id: str
    airline: str
    airline_code: str
    price: str
    currency: str
    status: str


class HackathonRate(BaseModel):
    rate_id: str
    airline: str
    airline_code: str
    origin: str
    destination: str
    weight_kg: str
    chargeable_weight_kg: str
    total: str
    currency: str
    fx_eur_per_usd: str | None = None
    transit_days: int
    valid_until: str
    dataset_version: str
