import logging
import re
from dataclasses import dataclass, field
from typing import Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)

ACCEPT = "accept"
QUESTION = "question"
DECLINE = "decline"
UNCLEAR = "unclear"

_QUOTE_MARKERS = re.compile(
    r"^\s*(on .{0,200}wrote:|am .{0,200}schrieb.{0,80}:|le .{0,200}a écrit\s*:|"
    r"el .{0,200}escribió\s*:|-{2,}\s*(original message|ursprüngliche nachricht)\s*-{2,}|"
    r"(from|von|de):\s.+@)",
    re.IGNORECASE,
)

# Strong phrases accept even when the mail also asks something; weak ones only on their own.
_ACCEPT_STRONG = re.compile(
    r"\b("
    # English
    r"we accept|i accept|we'll accept|we will accept|accept (your|the|this) (offer|quote|price)|"
    r"(offer|quote) accepted|please book|book it|please proceed|proceed with the booking|"
    r"you can proceed|confirm the booking|let's go with|we'll go with|we will go with|"
    r"we'll take it|we will take it|let's do it|it's a deal|that's a deal|happy to proceed|"
    # German
    r"wir akzeptieren|ich akzeptiere|angebot (ist )?angenommen|"
    r"nehmen (das|ihr|ihre|dieses) angebot an|bitte buchen|buchen sie|"
    # French
    r"nous acceptons|j'accepte|veuillez réserver|merci de réserver|"
    # Spanish / Portuguese
    r"aceptamos|acepto (la|su|el)|por favor reserv[ae]|aceitamos|"
    # Italian
    r"accettiamo|accetto (la|il|l')|"
    # Dutch
    r"wij accepteren|we accepteren|graag boeken"
    r")\b",
    re.IGNORECASE,
)
_ACCEPT_WEAK = re.compile(
    r"\b(go ahead|sounds good|works for us|einverstanden|d'accord|de acuerdo|va bene|"
    r"procedete|akkoord)\b",
    re.IGNORECASE,
)
_NEGATED_ACCEPT = re.compile(
    r"\b(do not|don't|dont|cannot|can't|cant|will not|won't|not able to|unable to)\s+"
    r"(accept|proceed|book|confirm|go ahead)|"
    r"\b(nicht (an|annehmen|akzeptieren|buchen)|nicht einverstanden|"
    r"n'acceptons pas|pas d'accord|no aceptamos|no estamos de acuerdo|non accettiamo)\b",
    re.IGNORECASE,
)
_DECLINE = re.compile(
    r"\b(decline|declined|reject|rejected|too expensive|no longer need|not interested|"
    r"found another|went with another|we will pass|we'll pass|cancel|above our budget|"
    r"zu teuer|kein interesse|lehnen .{0,40}ab|absagen|"
    r"trop cher|nous refusons|pas intéressés?|"
    r"demasiado caro|rechazamos|no nos interesa|troppo caro|te duur)",
    re.IGNORECASE,
)
# Talking about accepting is not accepting.
_TENTATIVE = re.compile(
    r"\b(before (we|i) (accept|book|confirm|go ahead)|"
    r"(should|shall|can|could|may|would) (we|i|you) (accept|book|go ahead|proceed)|"
    r"if (we|i) (accept|go ahead|book))\b",
    re.IGNORECASE,
)
_CONDITIONAL = re.compile(
    r"\b(as long as|provided (that)?|only if|on condition|subject to|if you can|assuming|"
    r"unter der bedingung|sofern|vorausgesetzt|falls|à condition|siempre que|a condizione)\b",
    re.IGNORECASE,
)
_QUESTION_WORDS = re.compile(
    r"\b(could you|can you|would you|does (the|this|it)|is (the|this|it)|are (the|there)|"
    r"what|when|how|which|why|include[sd]?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReplyContext:
    sender: str
    subject: str
    body: str
    quote_total: str | None = None
    quote_currency: str | None = None


@dataclass(frozen=True)
class ReplyJudgment:
    verdict: str
    decided_by: str
    model: str | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    error: str | None = None


class ReplyJudge(Protocol):
    def judge(self, reply: ReplyContext) -> ReplyJudgment: ...


def reply_text(body: str) -> str:
    """The customer's own words: drops quoted lines and the quoted earlier mail."""
    kept = []
    for line in (body or "").splitlines():
        if _QUOTE_MARKERS.match(line):
            break
        if line.lstrip().startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def keyword_verdict(subject: str, body: str) -> str:
    text = reply_text(body) or (subject or "")
    if _NEGATED_ACCEPT.search(text) or _DECLINE.search(text):
        return DECLINE
    tentative = bool(_TENTATIVE.search(text))
    conditional = bool(_CONDITIONAL.search(text))
    asks = tentative or "?" in text or bool(_QUESTION_WORDS.search(text))
    strong = bool(_ACCEPT_STRONG.search(text))
    weak = bool(_ACCEPT_WEAK.search(text)) and not asks
    accepts = (strong or weak) and not tentative
    if accepts and not conditional:
        return ACCEPT
    if accepts:
        return UNCLEAR
    if asks:
        return QUESTION
    return UNCLEAR


class KeywordJudge:
    def judge(self, reply: ReplyContext) -> ReplyJudgment:
        verdict = keyword_verdict(reply.subject, reply.body)
        return ReplyJudgment(verdict=verdict, decided_by="keyword")


def get_reply_judge(db=None) -> ReplyJudge:
    settings = get_settings()
    if settings.router_backend == "gateway":
        from app.platform.gateway import LiveGateway
        from app.platform.reply_model import GatewayJudge

        return GatewayJudge(LiveGateway(db=db), settings.router_model)
    return KeywordJudge()
