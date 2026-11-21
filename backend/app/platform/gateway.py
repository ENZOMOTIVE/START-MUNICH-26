from typing import Any

import httpx

from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db.models import GatewayUsageRow

CHAT_COMPLETIONS = "/v1/gateway/chat/completions"
HTTP_TIMEOUT_SECONDS = 5.0


class GatewayError(Exception):
    pass


def _record_usage(db, model: str, simulated: bool) -> None:
    row = GatewayUsageRow(model=model, cost_kind="unknown", simulated=simulated)
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
        _record_usage(self._db, str(data.get("model") or model), simulated=False)
        return {
            "simulated": False,
            "text": text,
            "model": data.get("model") or model,
            "usage": data.get("usage") or {},
            "logprobs": (choice.get("logprobs") or {}).get("content"),
        }
