from app.pricing.calculator import select_offer


def choose_offer(candidates, service_intent):
    return select_offer(candidates, service_intent)
