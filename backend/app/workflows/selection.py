from datetime import UTC, datetime

from app.contracts.models import ServiceIntent
from app.core import clock
from app.core.errors import DomainError
from app.pricing.calculator import select_offer


def _intent_value(service_intent) -> str:
    value = getattr(service_intent, "value", service_intent)
    value = str(value or ServiceIntent.CHEAPEST).strip().lower()
    if value not in {ServiceIntent.CHEAPEST.value, ServiceIntent.FASTEST.value}:
        raise DomainError("invalid_service_intent")
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _is_current(candidate, now: datetime) -> bool:
    valid_until = getattr(candidate, "valid_until", None)
    return valid_until is None or _aware(valid_until) > now


def choose_offer(candidates, service_intent, *, now: datetime | None = None):
    candidates = list(candidates)
    intent = _intent_value(service_intent)
    now = _aware(now or clock.now())
    complete = [candidate for candidate in candidates if candidate.complete]
    current = [candidate for candidate in complete if _is_current(candidate, now)]
    if complete and not current:
        raise DomainError("offers_expired")
    return select_offer(current, intent)


def choose_offer_with_evidence(candidates, service_intent, *, now: datetime | None = None):
    candidates = list(candidates)
    intent = _intent_value(service_intent)
    now = _aware(now or clock.now())
    complete = [candidate for candidate in candidates if candidate.complete]
    current = [candidate for candidate in complete if _is_current(candidate, now)]
    if complete and not current:
        raise DomainError("offers_expired")

    # Quote what can be booked: an offer without a portal rate_id is only used when nothing
    # bookable exists (booking it then needs a person; email booking is not built).
    bookable = [candidate for candidate in current if candidate.rate_id]
    unbookable = {str(c.id) for c in current if not c.rate_id} if bookable else set()
    current = bookable or current
    selected = select_offer(current, intent)
    missing_transit = (
        [candidate for candidate in current if candidate.transit_days is None]
        if intent == ServiceIntent.FASTEST.value
        else []
    )
    considered = (
        [candidate for candidate in current if candidate.transit_days is not None]
        if intent == ServiceIntent.FASTEST.value
        else current
    )
    evidence = {
        "objective": intent,
        "rule": (
            "minimum_transit_days_among_complete_current_candidates"
            if intent == ServiceIntent.FASTEST.value
            else "minimum_total_among_complete_current_candidates"
        ),
        "considered_candidate_ids": [str(candidate.id) for candidate in considered],
        "excluded_incomplete_candidate_ids": [
            str(candidate.id) for candidate in candidates if not candidate.complete
        ],
        "excluded_expired_candidate_ids": [
            str(candidate.id)
            for candidate in complete
            if not _is_current(candidate, now)
        ],
        "excluded_missing_transit_candidate_ids": [
            str(candidate.id) for candidate in missing_transit
        ],
        "candidate_assessment": [
            {
                "candidate_id": str(candidate.id),
                "rate_id": candidate.rate_id,
                "carrier": candidate.carrier,
                "total": str(candidate.total),
                "currency": candidate.currency,
                "transit_days": candidate.transit_days,
                "valid_until": (
                    _aware(candidate.valid_until).isoformat()
                    if candidate.valid_until is not None
                    else None
                ),
                "result": (
                    "excluded_incomplete"
                    if not candidate.complete
                    else "excluded_expired"
                    if not _is_current(candidate, now)
                    else "excluded_missing_transit"
                    if intent == ServiceIntent.FASTEST.value
                    and candidate.transit_days is None
                    else "selected"
                    if candidate.id == selected.id
                    else "excluded_unbookable"
                    if str(candidate.id) in unbookable
                    else "considered"
                ),
            }
            for candidate in candidates
        ],
        "excluded_unbookable_candidate_ids": sorted(unbookable),
        "selected_candidate_id": str(selected.id),
        "selected_rate_id": selected.rate_id,
        "selected_total": str(selected.total),
        "selected_currency": selected.currency,
        "selected_transit_days": selected.transit_days,
        "source_ref": selected.source_ref,
        "source_version": selected.source_version,
        "valid_until": (
            _aware(selected.valid_until).isoformat() if selected.valid_until is not None else None
        ),
        "charges": selected.charges or [],
        "simulated": selected.simulated,
    }
    return selected, evidence
