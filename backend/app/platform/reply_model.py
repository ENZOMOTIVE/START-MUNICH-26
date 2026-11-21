"""The gateway model's part of reading a reply to a quote: wording, labels and threshold."""

import logging
import math

from app.core.errors import DomainError
from app.platform.decisions import (
    ACCEPT,
    DECLINE,
    QUESTION,
    UNCLEAR,
    KeywordJudge,
    ReplyContext,
    ReplyJudge,
    ReplyJudgment,
    reply_text,
)
from app.platform.gateway import GatewayError

log = logging.getLogger(__name__)

VERDICT_FOR_LABEL = {"A": ACCEPT, "Q": QUESTION, "D": DECLINE, "U": UNCLEAR}
THRESHOLD = 0.7

SYSTEM_PROMPT = (
    "You read a customer's reply to an air freight quote and answer with one capital letter.\n"
    "A: the customer accepts the quote and wants the shipment booked.\n"
    "Q: the customer asks something about the quote or the shipment without accepting it.\n"
    "D: the customer declines the quote or no longer needs the shipment.\n"
    "U: none of these, or you cannot tell.\n"
    "An acceptance that depends on a condition is U. "
    "The reply may be in any language. Answer with the letter only."
)


def build_messages(reply: ReplyContext) -> list[dict[str, str]]:
    quote = f"{reply.quote_total} {reply.quote_currency}" if reply.quote_total else "unknown"
    user = f"Quoted total: {quote}\nSubject: {reply.subject}\nReply:\n{reply_text(reply.body)}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


REQUEST_PARAMS = {"temperature": 0, "max_tokens": 1, "logprobs": True, "top_logprobs": 5}


def label_probabilities(logprobs: list | None) -> dict[str, float]:
    if not logprobs:
        return {}
    probabilities: dict[str, float] = {}
    for candidate in logprobs[0].get("top_logprobs") or []:
        label = str(candidate.get("token", "")).strip().upper()
        if label in VERDICT_FOR_LABEL:
            probabilities[label] = probabilities.get(label, 0.0) + math.exp(candidate["logprob"])
    return {label: round(p, 4) for label, p in probabilities.items()}


def read_answer(text: str, logprobs: list | None) -> tuple[str, dict[str, float]]:
    """Map the model's answer to a verdict; raises ValueError for an unexpected label."""
    label = text.strip().upper()[:1]
    if label not in VERDICT_FOR_LABEL:
        raise ValueError(f"unexpected label {text!r}")
    probabilities = label_probabilities(logprobs)
    if probabilities and probabilities.get(label, 0.0) < THRESHOLD:
        return UNCLEAR, probabilities
    return VERDICT_FOR_LABEL[label], probabilities


class GatewayJudge:
    """A keyword acceptance is final; any other reply to a quote is asked of the gateway model."""

    def __init__(self, gateway, model: str, keyword: ReplyJudge | None = None):
        self.gateway = gateway
        self.model = model
        self.keyword = keyword or KeywordJudge()

    def judge(self, reply: ReplyContext) -> ReplyJudgment:
        first = self.keyword.judge(reply)
        if first.verdict == ACCEPT:
            return first
        if not self.model:
            return self._fall_back(first, "ROUTER_MODEL is not set")
        try:
            answer = self.gateway.complete(
                model=self.model, messages=build_messages(reply), **REQUEST_PARAMS
            )
            verdict, probabilities = read_answer(answer["text"], answer.get("logprobs"))
        except (GatewayError, DomainError, ValueError) as exc:
            return self._fall_back(first, f"{type(exc).__name__}: {exc}")
        log.info(
            "reply model=%s text=%r probabilities=%s", self.model, answer["text"], probabilities
        )
        return ReplyJudgment(
            verdict=verdict,
            decided_by="gateway",
            model=str(answer.get("model") or self.model),
            probabilities=probabilities,
        )

    def _fall_back(self, keyword: ReplyJudgment, error: str) -> ReplyJudgment:
        log.warning("reply model unavailable, keeping keyword answer: %s", error)
        return ReplyJudgment(
            verdict=keyword.verdict, decided_by="fallback", model=self.model or None, error=error
        )
