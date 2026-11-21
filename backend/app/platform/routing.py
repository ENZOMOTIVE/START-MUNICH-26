import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.platform.carriers import carrier_for, normalize_address
from app.platform.decisions import ACCEPT, DECLINE, QUESTION, ReplyContext, ReplyJudge

TRACKING_ADDRESS = "tracking@hackloop.mail"
RATE_DESK_ADDRESS = "ratedesk@hackloop.mail"

# We put this in the subject of every enquiry; the carrier's reply carries it back.
ENQUIRY_REF = re.compile(r"\[ref ([0-9a-f]{8}-[0-9a-f]{4})\]")
# Subject documented for portal booking confirmations: "Booking <booking_reference> confirmed".
BOOKING_CONFIRMED = re.compile(r"\bbooking\s+BK-[A-Z0-9]{8}\s+confirmed\b", re.IGNORECASE)

RFQ_AGENT = "rfq_booking"
TRACKING_AGENT = "track_trace"
TARIFF_AGENT = "tariff_ingestion"

AGENT_FOR_KIND: dict[str, str | None] = {
    "rfq": RFQ_AGENT,
    "acceptance": RFQ_AGENT,
    "follow_up": RFQ_AGENT,
    "carrier_bid": RFQ_AGENT,
    "rate_desk_reply": RFQ_AGENT,
    "booking_confirmation": RFQ_AGENT,
    "tracking": TRACKING_AGENT,
    "tariff": TARIFF_AGENT,
    "unknown": None,
}
KINDS = frozenset(AGENT_FOR_KIND)

_KIND_FOR_VERDICT = {ACCEPT: "acceptance", QUESTION: "follow_up", DECLINE: "follow_up"}


@dataclass(frozen=True)
class MailFacts:
    sender: str
    subject: str
    body: str
    has_attachments: bool
    starts_thread: bool
    preset_kind: str | None = None
    thread_stage: str | None = None
    quote_total: str | None = None
    quote_currency: str | None = None
    answers_enquiry: bool = False  # the subject carries the reference of a request of ours


@dataclass(frozen=True)
class RoutingDecision:
    kind: str
    decided_by: str
    rule: str | None
    sender: dict[str, str | None]
    starts_thread: bool
    model: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    error: str | None = None

    @property
    def agent(self) -> str | None:
        return AGENT_FOR_KIND[self.kind]

    def record(self) -> dict[str, Any]:
        return {**asdict(self), "agent": self.agent}


def sender_info(address: str) -> dict[str, str | None]:
    email = normalize_address(address)
    if email == TRACKING_ADDRESS:
        return {"email": email, "name": None, "kind": "tracking"}
    if email == RATE_DESK_ADDRESS:
        return {"email": email, "name": None, "kind": "rate_desk"}
    carrier = carrier_for(email)
    if carrier is not None:
        return {"email": email, "name": carrier.name, "kind": carrier.kind}
    return {"email": email, "name": None, "kind": "customer"}


def decide(facts: MailFacts, judge: ReplyJudge) -> RoutingDecision:
    sender = sender_info(facts.sender)

    def done(kind: str, decided_by: str, rule: str | None, **extra) -> RoutingDecision:
        return RoutingDecision(
            kind=kind,
            decided_by=decided_by,
            rule=rule,
            sender=sender,
            starts_thread=facts.starts_thread,
            **extra,
        )

    if facts.preset_kind in KINDS and facts.preset_kind != "unknown":
        return done(facts.preset_kind, "preset", "classification_preset")

    if BOOKING_CONFIRMED.search(facts.subject or ""):
        return done("booking_confirmation", "sender_rule", "booking_confirmation_subject")
    if sender["kind"] == "tracking":
        return done("tracking", "sender_rule", "tracking_address")
    if sender["kind"] == "rate_desk":
        return done("rate_desk_reply", "sender_rule", "rate_desk_address")
    if sender["kind"] == "airline" and facts.has_attachments and not facts.answers_enquiry:
        return done("tariff", "sender_rule", "airline_with_attachment")
    if sender["kind"] in {"airline", "trucker"}:
        return done("carrier_bid", "sender_rule", "carrier_address")

    if facts.starts_thread:
        return done("rfq", "thread_rule", "customer_new_thread")
    if facts.thread_stage != "quoted":
        return done("follow_up", "thread_rule", "customer_reply_no_open_quote")

    judgment = judge.judge(
        ReplyContext(
            sender=facts.sender,
            subject=facts.subject,
            body=facts.body,
            quote_total=facts.quote_total,
            quote_currency=facts.quote_currency,
        )
    )
    return done(
        _KIND_FOR_VERDICT.get(judgment.verdict, "unknown"),
        judgment.decided_by,
        "customer_reply_to_quote",
        model=judgment.model,
        probabilities=dict(judgment.probabilities),
        error=judgment.error,
    )
