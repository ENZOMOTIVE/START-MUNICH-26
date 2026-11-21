from typing import Any

from app.core.config import get_settings
from app.core.errors import LiveActionsDisabled
from app.db.models import GatewayUsageRow


class FakeGateway:
    def __init__(self, db=None):
        self._db = db

    def complete(self, prompt: str, model: str | None = None) -> dict[str, Any]:
        row = GatewayUsageRow(
            model=model or "fixture",
            cost_kind="unknown",
            simulated=True,
        )
        if self._db is not None:
            self._db.add(row)
            self._db.flush()
        else:
            from app.db.session import session_scope

            with session_scope() as db:
                db.add(row)
        return {"simulated": True, "text": "fixture-extract", "cost_kind": "unknown"}


class LiveGateway:
    def complete(self, prompt: str, model: str | None = None) -> dict[str, Any]:
        if not get_settings().live_allowed():
            raise LiveActionsDisabled()
        raise NotImplementedError(
            "TODO: implement POST /v1/gateway/chat/completions "
            "after re-reading official OpenAPI. Do not invent endpoints."
        )
