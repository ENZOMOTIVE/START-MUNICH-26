from decimal import Decimal

from app.contracts.models import ServiceIntent
from app.core.errors import DomainError


def select_offer(candidates, intent):
    complete = [c for c in candidates if c.complete]
    if not complete:
        raise DomainError("no_complete_candidates")
    intent_value = intent
    if hasattr(intent, "value"):
        intent_value = intent.value
    intent_value = str(intent_value or ServiceIntent.CHEAPEST).lower()
    if intent_value == ServiceIntent.FASTEST or intent_value == "fastest":
        with_transit = [c for c in complete if c.transit_days is not None]
        if not with_transit:
            raise DomainError("missing_transit")
        return min(with_transit, key=lambda c: (c.transit_days, Decimal(str(c.total))))
    return min(complete, key=lambda c: Decimal(str(c.total)))
