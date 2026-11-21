"""Customer emails built only from recorded facts.

Every function takes the facts it may state and returns {"purpose", "subject", "body"}.
Nothing here looks anything up, so a reply cannot contain a fact the workflow did not record.
Only `final_quote` may state a price.
"""

from datetime import datetime

SIGNATURE = "Best regards,\nFiveU Freight Desk"
_ASK = {
    "origin": "the departure airport",
    "destination": "the destination airport",
    "weight_kg": "the gross weight in kg",
    "pickup_zone": "the postcode or zone we should collect from",
    "volume_m3": "the total volume in cbm (or the dimensions and number of pieces)",
}
_DOC = {
    "dgd": "the Shipper's Declaration for Dangerous Goods (DGD)",
    "msds": "the safety data sheet (MSDS)",
}


def _join(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _mail(purpose: str, subject: str, name: str | None, *paragraphs: str) -> dict:
    greeting = f"Hello {name}," if name else "Hello,"
    subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    body = "\n\n".join([greeting, *[p for p in paragraphs if p], SIGNATURE])
    return {"purpose": purpose, "subject": subject, "body": body}


def _route(origin: str | None, destination: str | None) -> str:
    if origin and destination:
        return f" from {origin} to {destination}"
    return f" from {origin}" if origin else (f" to {destination}" if destination else "")


def _when(value: datetime) -> str:
    return value.strftime("%d %b %Y %H:%M UTC")


def missing_info(name, subject, origin, destination, missing: list[str], notes: list[str]) -> dict:
    note = f" ({'; '.join(notes)}.)" if notes else ""
    return _mail(
        "missing_info",
        subject,
        name,
        f"Thank you for your request{_route(origin, destination)}.",
        f"To price it I still need {_join([_ASK[m] for m in missing])}.{note}",
        "As soon as I have that, I will send you the quote.",
    )


def dg_documents(name, subject, origin, destination, signals: list, missing: list) -> dict:
    return _mail(
        "dg_documents",
        subject,
        name,
        f"Thank you for your request{_route(origin, destination)}.",
        f"Your message mentions {_join(signals[:3])}, which ships as dangerous goods. "
        f"Before I can quote, I need {_join([_DOC[m] for m in missing])}.",
        "Please reply to this email with the documents attached and I will continue right away.",
    )


def dg_conflict(name, subject, origin, destination, signals: list[str], missing: list[str]) -> dict:
    return _mail(
        "dg_conflict",
        subject,
        name,
        f"Thank you for your request{_route(origin, destination)}.",
        f"Your message describes the cargo as non-dangerous, but it also mentions "
        f"{_join(signals[:3])}. Could you confirm which is correct?",
        f"If it is dangerous goods, please attach {_join([_DOC[m] for m in missing])}. "
        "If it is not, a short confirmation is enough and I will quote straight away.",
    )


def final_quote(
    name,
    subject,
    origin,
    destination,
    weight_kg,
    carrier,
    total,
    currency,
    transit_days,
    charge_codes: list[str],
    valid_until: datetime | None,
    pickup_zone=None,
) -> dict:
    included = [c.replace("_", " ") for c in charge_codes]
    transit = "transit time not confirmed"
    if transit_days is not None:
        transit = f"transit {transit_days} days"
    lines = [f"Total: {currency} {total} all-in", f"Carrier: {carrier}, {transit}"]
    if included:
        lines.append(f"Included: {_join(included)}")
    if pickup_zone:
        lines.append(f"Pickup: from zone {pickup_zone}")
    if valid_until is not None:
        lines.append(f"Valid until: {_when(valid_until)}")
    return _mail(
        "first_quote",
        subject,
        name,
        "Thank you for your request. "
        f"Here is our offer for {weight_kg} kg{_route(origin, destination)}:",
        "\n".join(lines),
        "To book, reply to this email and confirm that you accept.",
    )


def pickup_enquiry(zone, airport, weight_kg, volume_m3, ref: str, postcode=None) -> dict:
    """To a trucker. `ref` in the subject is how the reply is matched back to the request.

    Live truckers price by the full postcode: asked for "zone 80" they answered that they
    do not serve "pickup postcode 80". The zone is only named when no postcode is known.
    """
    cargo = f"{weight_kg} kg" + (f", {volume_m3} cbm" if volume_m3 else "")
    place = f"postcode {postcode}" if postcode else f"pickup zone {zone}"
    body = "\n\n".join(
        [
            "Hello,",
            "Please send us your price for one collection:",
            f"From: {place}\nTo: {airport} airport\nCargo: {cargo}",
            "Please reply to this email with one all-in price and its currency.",
            SIGNATURE,
        ]
    )
    subject = f"Pickup enquiry: {postcode or f'zone {zone}'} to {airport} [ref {ref}]"
    return {"purpose": "carrier_enquiry", "subject": subject, "body": body}


def rate_enquiry(origin, destination, weight_kg, volume_m3, dangerous: list[str], ref: str) -> dict:
    """To an airline or the rate desk. `ref` in the subject matches the reply to the request."""
    cargo = f"{weight_kg} kg" + (f", {volume_m3} cbm" if volume_m3 else "")
    if dangerous:
        cargo += f"\nDangerous goods: {', '.join(dangerous)}"
    body = "\n\n".join(
        [
            "Hello,",
            "Please send us your rate for one air freight shipment:",
            f"Route: {origin} to {destination}\nCargo: {cargo}",
            "Please reply to this email with one all-in price and its currency, restate the "
            "route, and tell us until when the price is valid.",
            SIGNATURE,
        ]
    )
    subject = f"Rate enquiry {origin}-{destination}, {weight_kg} kg [ref {ref}]"
    return {"purpose": "rate_enquiry", "subject": subject, "body": body}


def carrier_booking_request(
    origin, destination, weight_kg, total, currency, team_reference
) -> dict:
    """To an airline that is not bookable on the portal."""
    body = "\n\n".join(
        [
            "Hello,",
            "Please book the following shipment at your tariff rate:",
            f"Route: {origin} to {destination}\nCargo: {weight_kg} kg\n"
            f"Agreed price: {currency} {total}\nOur reference: {team_reference}",
            "Please confirm with your booking reference.",
            SIGNATURE,
        ]
    )
    subject = f"Booking request {origin}-{destination} [ref {team_reference}]"
    return {"purpose": "carrier_booking", "subject": subject, "body": body}


def booking_failed(name, subject, carrier, reason: str) -> dict:
    return _mail(
        "booking_failed",
        subject,
        name,
        f"I am sorry, I could not complete the booking with {carrier}: {reason}.",
        "Nothing has been booked. If you still want to ship, reply to this email and I will "
        "send you an updated offer.",
    )


_NOT_AS_QUOTED = {
    "amount_differs_from_quote": "it names a different price from the one I quoted",
    "route_differs_from_quote": "it names a different route from the one I quoted",
    "conditional_or_deferred": "it adds a condition or asks me to wait",
    "service_differs_from_quote": "it asks for a different service from the one I quoted",
}


def acceptance_clarification(name, subject, carrier, problem: str) -> dict:
    """No price is repeated here: the only priced message in a thread is the quote itself."""
    return _mail(
        "acceptance_clarification",
        subject,
        name,
        f"Thank you for your reply. I have not booked anything yet, because "
        f"{_NOT_AS_QUOTED[problem]}.",
        f"If you accept the offer with {carrier} exactly as quoted, please reply "
        '"We accept the quote" and I will book it. If you need a change, tell me what you '
        "need and I will send a revised offer.",
    )


def quote_answer(
    name, subject, carrier, transit_days, charge_codes: list[str], valid_until, pickup_from, origin
) -> dict:
    """Answers a question about the quote we sent. It repeats no price, so the quote stays the
    only priced message in the thread."""
    included = _join([c.replace("_", " ") for c in charge_codes] or ["the air freight"])
    pickup = (
        f"Pickup from {pickup_from} to {origin} airport is included."
        if pickup_from
        else f"Pickup is not included: the price starts at {origin} airport."
    )
    transit = (
        f"{carrier} needs {transit_days} days in transit"
        if transit_days is not None
        else f"{carrier} has not confirmed a transit time"
    )
    valid = f", and the offer is valid until {_when(valid_until)}" if valid_until else ""
    return _mail(
        "quote_answer",
        subject,
        name,
        "Thank you for your question.",
        f"The total in my quote is all-in. It covers {included}, and nothing comes on top. "
        f"{pickup}",
        f"{transit}{valid}.",
        "To book, reply to this email and confirm that you accept.",
    )


def no_offer(name, subject, origin, destination, reason: str) -> dict:
    return _mail(
        "no_offer",
        subject,
        name,
        f"Thank you for your request{_route(origin, destination)}.",
        f"I am sorry, I cannot offer a price right now: {reason}. "
        "I cannot say yet when a rate will be available.",
        "If anything about the shipment can change, tell me and I will check again.",
    )


def booking_submitted(name, subject, carrier, team_reference) -> dict:
    return _mail(
        "booking_submitted",
        subject,
        name,
        f"Thank you for accepting. I have submitted the booking with {carrier} "
        f"under our reference {team_reference}.",
        "It is not confirmed yet. I will write again as soon as the carrier confirms it.",
    )


def booking_confirmed(
    name, subject, carrier, booking_reference, origin, destination, weight_kg, total, currency
) -> dict:
    return _mail(
        "booking_confirmed",
        subject,
        name,
        f"Your booking is confirmed. Booking reference: {booking_reference}.",
        f"{weight_kg} kg{_route(origin, destination)} with {carrier}, {currency} {total} all-in.",
        "I will pass on tracking updates as I receive them.",
    )


def tracking_answer(
    name, subject, booking_reference, booking_status, carrier, update: dict | None
) -> dict:
    if booking_status != "confirmed":
        return _mail(
            "tracking_answer",
            subject,
            name,
            f"Your booking with {carrier} has been submitted but is not confirmed yet, "
            "so there is no shipment status to report.",
            "I will write as soon as the carrier confirms it.",
        )
    if update is None:
        return _mail(
            "tracking_answer",
            subject,
            name,
            f"Your booking {booking_reference} with {carrier} is confirmed.",
            "I have not received a tracking update for it yet, so I cannot tell you where the "
            "shipment is right now. I will pass on updates as soon as they arrive.",
        )
    return _mail(
        "tracking_answer",
        subject,
        name,
        f"The latest confirmed update for booking {booking_reference} is: "
        f"{update['status']} (reported {_when(update['reported_at'])}).",
        "I have no later information than that. I will pass on the next update when it arrives.",
    )
