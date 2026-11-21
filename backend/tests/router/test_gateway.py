import json
import math

import httpx
import pytest
from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db.models import GatewayUsageRow
from app.platform.decisions import (
    ACCEPT,
    DECLINE,
    QUESTION,
    UNCLEAR,
    KeywordJudge,
    ReplyContext,
    get_reply_judge,
)
from app.platform.gateway import GatewayError, LiveGateway
from app.platform.reply_model import GatewayJudge, build_messages, read_answer
from sqlalchemy import select


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("LIVE_ACTIONS_ENABLED", "true")
    monkeypatch.setenv("HACKATHON_API_KEY", "hk_test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.example.test",
        headers={"X-API-Key": "hk_test"},
        transport=httpx.MockTransport(handler),
    )


def _completion(label: str, probabilities: dict[str, float] | None = None) -> dict:
    choice = {"index": 0, "message": {"role": "assistant", "content": label}}
    if probabilities is not None:
        top = [{"token": k, "logprob": math.log(v)} for k, v in probabilities.items()]
        choice["logprobs"] = {"content": [{"token": label, "top_logprobs": top}]}
    return {
        "model": "small-model-2026",
        "choices": [choice],
        "usage": {"prompt_tokens": 120, "completion_tokens": 1, "total_tokens": 121},
    }


def _reply(body: str) -> ReplyContext:
    return ReplyContext("buyer@shipper.example", "Re: Quote", body, "412.50", "EUR")


class StubGateway:
    def __init__(self, answer=None, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.answer


def test_live_completion_returns_text_usage_and_logprobs(db, live):
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["path"] = request.url.path
        sent["key"] = request.headers["X-API-Key"]
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion("A", {"A": 0.9, "Q": 0.1}))

    before = db.execute(select(GatewayUsageRow)).scalars().all()
    result = LiveGateway(db=db, client=_client(handler)).complete(
        model="small-model", messages=[{"role": "user", "content": "hi"}], max_tokens=1
    )
    after = db.execute(select(GatewayUsageRow)).scalars().all()
    assert sent["path"] == "/v1/gateway/chat/completions"
    assert sent["key"] == "hk_test"
    assert sent["body"] == {
        "model": "small-model",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
    assert result["text"] == "A"
    assert result["usage"]["total_tokens"] == 121
    assert result["logprobs"][0]["top_logprobs"][0]["token"] == "A"
    new = [row for row in after if row.id not in {r.id for r in before}]
    assert [(row.model, row.simulated) for row in new] == [("small-model-2026", False)]


def test_live_completion_http_error(db, live):
    gateway = LiveGateway(db=db, client=_client(lambda r: httpx.Response(401, json={})))
    with pytest.raises(GatewayError, match="HTTP 401"):
        gateway.complete(model="m", prompt="hi")


def test_live_completion_timeout(db, live):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(GatewayError, match="ReadTimeout"):
        LiveGateway(db=db, client=_client(handler)).complete(model="m", prompt="hi")


def test_live_completion_refused_in_demo(db):
    get_settings.cache_clear()

    def handler(request):
        raise AssertionError("no request in demo")

    with pytest.raises(LiveActionsDisabled):
        LiveGateway(db=db, client=_client(handler)).complete(model="m", prompt="hi")


def test_messages_hold_only_the_customer_words():
    messages = build_messages(_reply("Bitte buchen.\n\nOn Sat Desk wrote:\n> Total 412.50"))
    assert messages[0]["role"] == "system"
    assert "Quoted total: 412.50 EUR" in messages[1]["content"]
    assert messages[1]["content"].endswith("Reply:\nBitte buchen.")


@pytest.mark.parametrize(
    ("text", "probabilities", "verdict"),
    [
        ("A", {"A": 0.95, "Q": 0.05}, ACCEPT),
        ("Q", {"Q": 0.80, "A": 0.15}, QUESTION),
        ("D", {"D": 0.90}, DECLINE),
        ("U", {"U": 0.99}, UNCLEAR),
        ("A", {"A": 0.55, "Q": 0.45}, UNCLEAR),
        (" a", None, ACCEPT),
    ],
)
def test_read_answer(text, probabilities, verdict):
    logprobs = None
    if probabilities is not None:
        top = [{"token": k, "logprob": math.log(v)} for k, v in probabilities.items()]
        logprobs = [{"token": text, "top_logprobs": top}]
    got, probs = read_answer(text, logprobs)
    assert got == verdict
    assert probs == (
        {} if probabilities is None else {k: round(v, 4) for k, v in probabilities.items()}
    )


def test_read_answer_rejects_unexpected_label():
    with pytest.raises(ValueError):
        read_answer("Yes", None)


def test_keyword_acceptance_makes_no_model_call():
    gateway = StubGateway(answer={"text": "D"})
    judgment = GatewayJudge(gateway, "m").judge(_reply("We accept your offer."))
    assert (judgment.verdict, judgment.decided_by) == (ACCEPT, "keyword")
    assert gateway.calls == []


def test_keyword_question_is_sent_to_model():
    answer = {
        "text": "A",
        "model": "small-model-2026",
        "logprobs": [{"token": "A", "top_logprobs": [{"token": "A", "logprob": math.log(0.9)}]}],
    }
    gateway = StubGateway(answer=answer)
    judgment = GatewayJudge(gateway, "small-model").judge(_reply("Grand, on y va. Merci!"))
    assert (judgment.verdict, judgment.decided_by) == (ACCEPT, "gateway")
    assert judgment.model == "small-model-2026"
    assert judgment.probabilities == {"A": 0.9}
    call = gateway.calls[0]
    assert (call["model"], call["temperature"], call["max_tokens"], call["logprobs"]) == (
        "small-model",
        0,
        1,
        True,
    )


@pytest.mark.parametrize(
    "error",
    [GatewayError("ReadTimeout: slow"), GatewayError("HTTP 500: oops"), LiveActionsDisabled()],
)
def test_model_failure_keeps_keyword_answer(error):
    judgment = GatewayJudge(StubGateway(error=error), "m").judge(
        _reply("Does the price include pickup?")
    )
    assert (judgment.verdict, judgment.decided_by) == (QUESTION, "fallback")
    assert judgment.error


def test_unexpected_label_keeps_keyword_answer():
    judgment = GatewayJudge(StubGateway(answer={"text": "Sure"}), "m").judge(
        _reply("Thanks for the quote.")
    )
    assert (judgment.verdict, judgment.decided_by) == (UNCLEAR, "fallback")


def test_missing_model_setting_keeps_keyword_answer():
    gateway = StubGateway(answer={"text": "A"})
    judgment = GatewayJudge(gateway, "").judge(_reply("Thanks for the quote."))
    assert judgment.decided_by == "fallback"
    assert "ROUTER_MODEL" in judgment.error
    assert gateway.calls == []


def test_backend_selection(monkeypatch):
    get_settings.cache_clear()
    assert isinstance(get_reply_judge(), KeywordJudge)
    monkeypatch.setenv("ROUTER_BACKEND", "gateway")
    monkeypatch.setenv("ROUTER_MODEL", "small-model")
    get_settings.cache_clear()
    try:
        judge = get_reply_judge()
        assert isinstance(judge, GatewayJudge)
        assert judge.model == "small-model"
    finally:
        get_settings.cache_clear()


def test_gateway_backend_in_demo_makes_no_request(monkeypatch):
    def fail(self, *args, **kwargs):
        raise AssertionError("no HTTP in demo")

    monkeypatch.setattr(httpx.Client, "request", fail)
    monkeypatch.setenv("ROUTER_BACKEND", "gateway")
    monkeypatch.setenv("ROUTER_MODEL", "small-model")
    get_settings.cache_clear()
    try:
        judgment = get_reply_judge().judge(_reply("Thanks for the quote."))
        assert (judgment.verdict, judgment.decided_by) == (UNCLEAR, "fallback")
    finally:
        get_settings.cache_clear()


def test_default_backend_makes_no_request(monkeypatch):
    def fail(self, *args, **kwargs):
        raise AssertionError("no HTTP in the default configuration")

    monkeypatch.setattr(httpx.Client, "request", fail)
    get_settings.cache_clear()
    assert get_reply_judge().judge(_reply("Thanks for the quote.")).verdict == UNCLEAR
