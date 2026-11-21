from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db.models import GatewayUsageRow
from app.platform import runlimits

CHAT_COMPLETIONS = "/v1/gateway/chat/completions"
HTTP_TIMEOUT_SECONDS = 5.0


class GatewayError(Exception):
    pass


def _record_usage(db, model: str, simulated: bool, usage: dict | None = None) -> None:
    row = GatewayUsageRow(model=model, cost_kind="unknown", simulated=simulated)
    settings = get_settings()
    if usage and settings.model_price_in_usd and model.startswith(settings.extraction_model):
        # An estimate from the reported tokens and the configured list price, never "observed".
        row.cost_kind = "estimated"
        row.estimated_cost = (
            usage.get("prompt_tokens", 0) * settings.model_price_in_usd
            + usage.get("completion_tokens", 0) * settings.model_price_out_usd
        ) / 1_000_000
    if db is not None:
        db.add(row)
        db.flush()
        return
    from app.db.session import session_scope

    with session_scope() as scope:
        scope.add(row)


class FakeGateway:
    def __init__(self, db=None):
        self._db = db

    def complete(self, prompt: str, model: str | None = None) -> dict[str, Any]:
        _record_usage(self._db, model or "fixture", simulated=True)
        return {"simulated": True, "text": "fixture-extract", "cost_kind": "unknown"}


class LiveGateway:
    def __init__(self, db=None, client: httpx.Client | None = None):
        self._db = db
        self._client = client

    def _http(self) -> httpx.Client:
        settings = get_settings()
        if not settings.live_allowed():
            raise LiveActionsDisabled()
        if self._client is None:
            self._client = httpx.Client(
                base_url=settings.hackathon_api_base,
                headers={"X-API-Key": settings.hackathon_api_key},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        return self._client

    def complete(
        self,
        prompt: str | None = None,
        model: str | None = None,
        *,
        messages: list[dict[str, str]] | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        if not model:
            raise GatewayError("no model given")
        client = self._http()
        body = {
            "model": model,
            "messages": messages or [{"role": "user", "content": prompt or ""}],
            **params,
        }
        from app.db.session import session_scope

        with session_scope() as scope:
            runlimits.require_running(scope)  # the global stop also stops paid model calls
            if get_settings().run_limits:
                # Bounded run: reserve the worst case (a generous token estimate for the
                # prompt, the full output cap), committed before the call is made.
                body.setdefault("max_tokens", 200)
                prompt_chars = sum(len(str(m.get("content") or "")) for m in body["messages"])
                runlimits.reserve_model_call(scope, prompt_chars // 3 + 50, body["max_tokens"])
        try:
            response = client.post(CHAT_COMPLETIONS, json=body)
        except httpx.TransportError as exc:
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise GatewayError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError(f"unexpected response: {exc!r}") from exc
        _record_usage(self._db, str(data.get("model") or model), False, data.get("usage"))
        return {
            "simulated": False,
            "text": text,
            "model": data.get("model") or model,
            "usage": data.get("usage") or {},
            "logprobs": (choice.get("logprobs") or {}).get("content"),
        }
