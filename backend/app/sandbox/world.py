"""An in-memory stand-in for the 5U API: mailbox, rate portal, bookings and model gateway.

The production adapters talk to it through `httpx.MockTransport`, so the real request, retry
and error-handling code runs, but no socket is ever opened. Paths and field names follow
docs/contracts.md; anything the real API does beyond that is unverified here.

Customers follow the explicit rules in their scenario. Their acceptance rules are local test
rules, not the organiser's evaluator.
"""

import hashlib
import heapq
import json
import random
import re
from collections import Counter
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app.platform.booking import PortalBooking
from app.platform.carriers import carrier_for
from app.platform.gateway import GatewayError, _record_usage
from app.platform.mailbox import LiveMailbox
from app.platform.routing import RATE_DESK_ADDRESS
from app.pricing.portal_client import PortalClient, PortalState

BASE_URL = "https://sandbox.invalid"
TEAM = "free-candy@hackloop.mail"
DEFAULT_CLOCK = "2026-11-21T09:00:00+00:00"
RFQ_LIFETIME = timedelta(minutes=6)
MODEL = "sandbox-fake-model"
_PRICE = re.compile(r"\b(EUR|USD)\s?(\d[\d,]*\.\d{2})\b")
_TOPICS = {
    "weight": ("weight",),
    "route": ("airport",),
    "pickup": ("postcode",),
    "dg_docs": ("declaration", "safety data sheet"),
    "dg_status": ("confirm which",),
}


def pdf_bytes(text: str) -> bytes:
    """A one-page PDF that really contains `text`, so a document reader has something to read."""
    lines = [
        line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        for line in text.splitlines()
    ]
    stream = "BT /F1 11 Tf 50 780 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    table = "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    size = len(objects) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n{table}".encode()
    return out + f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{len(out)}\n%%EOF\n".encode()


def attachment_bytes(attachment: dict) -> bytes | None:
    """`text` becomes a real PDF, `content` is served as it is, neither means the file is gone."""
    if "text" in attachment:
        return pdf_bytes(attachment["text"])
    content = attachment.get("content")
    return content.encode() if isinstance(content, str) else content


def _digest(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest().upper()


class World:
    def __init__(self, scenario: dict, seed: int, base_id: int):
        self.scenario, self.seed = scenario, seed
        self.rng = random.Random(f"{scenario['id']}@{scenario['version']}#{seed}")
        self.now = self.start = datetime.fromisoformat(scenario.get("clock", DEFAULT_CLOCK))
        self._id = base_id
        self._n = 0
        self._ticks = 0
        self.queue: list = []
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.keys: dict[int, str] = {}  # mailbox id -> scenario mail key
        self.threads: dict[int, str] = {}  # any mail id in a customer thread -> request key
        self.requests: dict[str, dict] = {}
        self.rates: dict[str, dict] = {}
        self.bookings: dict[str, dict] = {}
        self.book_requests: dict[str, int] = {}
        self.book_times: dict[str, int] = {}
        self.specs: dict[int, dict] = {}  # mailbox id -> the scenario mail it came from
        self.polls_with_mail = 0
        self.max_page = 0
        self.calls: Counter = Counter()  # requests the application made, by "METHOD path"
        self.portal_state = PortalState()  # one cache for the whole run, like one live worker
        self.share_portal_state = True
        self.faults = {k: list(v) for k, v in scenario.get("faults", {}).items()}
        self.fault_log: list[dict] = []
        self.model_calls: list[dict] = []
        self.stray: list[dict] = []  # outbound mail that is not a reply in a customer thread
        self.carrier_mail: list[dict] = []  # outbound mail to a trucker or airline in the scenario
        for mail in scenario["mails"]:
            self.schedule(mail, mail.get("at", 0))

    # ---------- time ----------
    def tick(self) -> datetime:
        """The simulated clock. Each reading is one microsecond later than the last, so records
        written at the same simulated second keep the order they were written in."""
        self._ticks += 1
        return self.now + timedelta(microseconds=self._ticks)

    def elapsed(self) -> int:
        return int((self.now - self.start).total_seconds())

    def schedule(self, mail: dict, delay: float) -> None:
        self._n += 1
        heapq.heappush(self.queue, (self.now + timedelta(seconds=delay), self._n, mail))

    def advance(self, until: datetime | None = None) -> None:
        target = self.now + timedelta(seconds=5)
        if self.queue:
            target = max(self.now + timedelta(seconds=1), self.queue[0][0])
        self.now = min(target, until) if until and until > self.now else target

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _fault(self, where: str):
        queue = self.faults.get(where)
        fault = queue.pop(0) if queue else None
        if fault:
            self.fault_log.append({"at_s": self.elapsed(), "where": where, "fault": fault})
        return fault

    # ---------- mailbox ----------
    def deliver_due(self) -> list[dict]:
        delivered = []
        while self.queue and self.queue[0][0] <= self.now:
            _, _, mail = heapq.heappop(self.queue)
            item_id = self._next_id()
            request = self.requests.get(mail.get("thread") or "")
            received = self.now + timedelta(seconds=mail.get("received_offset", 0))
            item = {
                "id": item_id,
                "from": mail["from"],
                "to": TEAM,
                "subject": mail["subject"],
                "body": mail["body"],
                "in_reply_to": request["last"] if request else mail.get("in_reply_to"),
                "references": [request["root"]] if request else mail.get("references", []),
                "attachments": [
                    {
                        "id": a["id"],
                        "filename": a["filename"],
                        "size": a.get("size", len(attachment_bytes(a) or b"")),
                    }
                    for a in mail.get("attachments", [])
                ],
                "received_at": received.isoformat(),
            }
            self.inbox.append(item)
            self.specs[item_id] = mail
            self.keys[item_id] = mail.get("key") or f"mail{len(self.inbox)}"
            if request:
                self.threads[item_id] = mail["thread"]
                request["last"] = item_id
            elif "customer" in mail:
                self.threads[item_id] = mail["key"]
                self.requests[mail["key"]] = {
                    "rules": mail["customer"],
                    "from": mail["from"],
                    "subject": mail["subject"],
                    "root": item_id,
                    "last": item_id,
                    "delivered_s": self.elapsed(),
                    "deadline": received + RFQ_LIFETIME,
                    "priced": [],
                    "received": [],
                    "questions": [],
                    "irrelevant_questions": 0,
                    "docs_requested": False,
                    "outcome": None,
                    "reason": None,
                    "tracking_answers": [],
                }
            delivered.append(item)
        return delivered

    def _poll(self, request: httpx.Request) -> httpx.Response:
        if self._fault("mailbox_poll") == "503":
            return httpx.Response(503, json={"detail": "sandbox fault"})
        since = int(request.url.params.get("since_id", 0))
        limit = min(int(request.url.params.get("limit", 200)), 200)
        page = [m for m in self.inbox if m["id"] > since][:limit]
        self.polls_with_mail += bool(page)
        self.max_page = max(self.max_page, len(page))
        return httpx.Response(
            200, json={"messages": page, "next_since_id": page[-1]["id"] if page else since}
        )

    def _send(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        fault = self._fault("mailbox_send")
        if fault == "not_sent":
            raise httpx.ConnectError("sandbox: connection refused")
        if fault == "429":  # over the send limit: nothing was sent, come back in 3 s
            return httpx.Response(429, headers={"Retry-After": "3"}, json={"detail": "limit"})
        item = {
            "id": self._next_id(),
            "to": body.get("to"),
            "subject": body.get("subject"),
            "body": body.get("body") or "",
            "in_reply_to": body.get("in_reply_to"),
            "at_s": self.elapsed(),
        }
        self.sent.append(item)
        self._customer_reads(item)
        if fault == "lost_ack":
            raise httpx.ReadTimeout("sandbox: the mail went out, the acknowledgement was lost")
        return httpx.Response(200, json={"id": item["id"]})

    # ---------- customers ----------
    def _reply(self, key: str, body: str, attachments=None) -> None:
        request = self.requests[key]
        mail = {
            "from": request["from"],
            "subject": f"Re: {request['subject']}",
            "body": body,
            "thread": key,
            "attachments": attachments or [],
        }
        self.schedule(mail, request["rules"].get("reply_delay") or self.rng.randint(15, 30))

    def _judge(self, request: dict, currency: str, amount: str, text: str) -> str | None:
        rules = request["rules"]
        if self.now > request["deadline"]:
            return "the quote arrived after the six-minute deadline"
        if rules.get("needs_documents") and not request["docs_requested"]:
            return "it was priced before the required dangerous-goods documents were requested"
        for fact in rules.get("truth", []):
            if fact not in text:
                return f"the quote does not match the request ({fact} is missing)"
        if currency != rules.get("currency", "EUR"):
            return f"the quote is in {currency}"
        if Decimal(amount.replace(",", "")) > Decimal(rules["budget"]):
            return f"{currency} {amount} is above our budget"
        return None

    def _carrier_reads(self, item: dict) -> None:
        """A trucker answers an enquiry with a price; an airline confirms a booking request.
        A carrier with no entry in the scenario, or an entry without a price, stays silent."""
        trucker = self.scenario.get("truckers", {}).get(item["to"])
        airline = self.scenario.get("airlines", {}).get(item["to"])
        if trucker is None and airline is None:
            # The mailbox accepts any listed carrier and the rate desk; one the scenario gives
            # no voice simply never answers. Any other address is a mail gone astray.
            listed = item["to"] == RATE_DESK_ADDRESS or carrier_for(item["to"]) is not None
            (self.carrier_mail if listed else self.stray).append(item)
            return
        self.carrier_mail.append(item)
        thread = {"references": [item["id"]], "in_reply_to": item["id"], "from": item["to"]}
        enquiry = re.match(r"Rate enquiry ([A-Z]{3})-([A-Z]{3}), (\S+) kg", item["subject"])
        if airline and enquiry and airline.get("bid"):
            bid = airline["bid"]
            text = bid.get("body") or (
                "Hello, {origin} to {destination}, {weight} kg: EUR {price} all-in, "
                "transit 4 days, valid until {valid}."
            )
            body = text.format(
                origin=enquiry.group(1),
                destination=enquiry.group(2),
                weight=enquiry.group(3),
                price=bid.get("price"),
                valid=(self.now + timedelta(days=2)).date().isoformat(),
                yesterday=(self.now - timedelta(days=1)).date().isoformat(),
            )
            sender = bid.get("from", item["to"])  # `from` lets a test answer as someone else
            reply = {"from": sender, "subject": f"Re: {item['subject']}", "body": body}
            self.schedule(
                reply if bid.get("detached") else {**thread, **reply}, bid.get("delay", 20)
            )
        if trucker and trucker.get("price"):
            body = f"Hello, we can collect this for EUR {trucker['price']} all-in."
            reply = {**thread, "subject": f"Re: {item['subject']}", "body": body}
            self.schedule(reply, trucker.get("delay", 20))
        reference = re.search(r"TM-[0-9a-f]{32}", item["body"])
        if airline and reference:
            booking = "BK-" + _digest(reference.group(0))[:8]
            body = f"Your booking is confirmed. Your reference: {reference.group(0)}"
            reply = {**thread, "subject": f"Booking {booking} confirmed", "body": body}
            self.schedule(reply, airline.get("delay", 30))

    def _attachment(self, message_id: int, attachment_id: int) -> httpx.Response:
        for attachment in self.specs.get(message_id, {}).get("attachments", []):
            content = attachment_bytes(attachment)
            if attachment.get("id") == attachment_id and content is not None:
                return httpx.Response(200, content=content)
        return httpx.Response(404, json={"detail": "no such attachment"})

    def _customer_reads(self, item: dict) -> None:
        key = self.threads.get(item["in_reply_to"])
        if key is None:
            self._carrier_reads(item)
            return
        self.threads[item["id"]] = key
        request, text, lower = self.requests[key], item["body"], item["body"].lower()
        request["last"] = item["id"]
        request["received"].append({"at_s": item["at_s"], "id": item["id"]})
        prices = _PRICE.findall(text)
        if "booking is confirmed" in lower:
            reference = re.search(r"BK-[A-Z0-9]+", text)
            for extra in request["rules"].get("after_confirmation", []):
                mail = dict(extra)
                for field in ("subject", "body"):
                    mail[field] = mail[field].replace(
                        "{booking_reference}", reference.group(0) if reference else "?"
                    )
                self.schedule(mail, mail.get("at", 20))
        elif "submitted the booking" in lower or "cannot offer a price" in lower:
            if "cannot offer" in lower:
                request["outcome"], request["reason"] = "no_offer", "no price was offered"
        elif (
            "update for booking" in lower
            or "tracking update" in lower
            or "not confirmed yet" in lower
        ):
            request["tracking_answers"].append(text)
        elif "have not booked anything yet" in lower:
            request["questions"].append(["acceptance_as_quoted"])  # the customer does not reply
        elif prices:
            request["priced"].append({"at_s": item["at_s"], "amount": prices[0][1]})
            if len(request["priced"]) > 1:
                return  # a second priced mail is a duplicate effect; the customer ignores it
            reason = self._judge(request, prices[0][0], prices[0][1], text)
            if reason is None:
                request["outcome"] = "accepted"
                for _ in range(2 if request["rules"].get("duplicate_accept") else 1):
                    self._reply(key, request["rules"].get("accept_text", "We accept the quote."))
            else:
                request["outcome"], request["reason"] = "rejected", reason
                self._reply(key, f"Thank you, but we will not proceed: {reason}.")
        else:
            answers = request["rules"].get("answers", {})
            asked = [t for t, words in _TOPICS.items() if any(w in lower for w in words)]
            known = []
            for topic in asked:
                if topic in answers:
                    # A list is what the customer says the first, second ... time it is asked.
                    turns = answers[topic] if isinstance(answers[topic], list) else [answers[topic]]
                    seen = sum(topic in earlier for earlier in request["questions"])
                    known.append(turns[min(seen, len(turns) - 1)])
            request["questions"].append(asked)
            if "dg_docs" in asked:
                request["docs_requested"] = True
            if known:
                files = [a for answer in known for a in answer.get("attachments", [])]
                self._reply(key, " ".join(answer["body"] for answer in known), files)
            else:
                request["irrelevant_questions"] += 1

    # ---------- rate portal ----------
    def _airline(self, origin: str, destination: str, code: str) -> dict | None:
        lanes = self.scenario.get("portal", {}).get("lanes", {})
        return next(
            (a for a in lanes.get(f"{origin}-{destination}", []) if a["code"] == code), None
        )

    def _ports(self) -> httpx.Response:
        lanes = self.scenario.get("portal", {}).get("lanes", {})
        return httpx.Response(
            200,
            json={
                "lanes": [
                    {
                        "origin": lane.split("-")[0],
                        "destination": lane.split("-")[1],
                        "airlines": [{"code": a["code"], "name": a["name"]} for a in airlines],
                    }
                    for lane, airlines in lanes.items()
                ]
            },
        )

    def _brackets(self, request: httpx.Request) -> httpx.Response:
        code = request.url.params["airline"]
        for airlines in self.scenario.get("portal", {}).get("lanes", {}).values():
            for airline in airlines:
                if airline["code"] == code:
                    breaks = sorted(int(b) for b in airline["per_kg"])
                    return httpx.Response(
                        200,
                        json={
                            "airline": airline["name"],
                            "airline_code": code,
                            "breaks_kg": breaks,
                        },
                    )
        return httpx.Response(404, json={"detail": "airline not on the portal"})

    def _rate(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        origin, destination, code = params["origin"], params["destination"], params["airline"]
        airline = self._airline(origin, destination, code)
        if airline is None:
            return httpx.Response(404, json={"detail": "no rate for this airline and lane"})
        fault = self._fault(f"rates:{code}")
        if fault == "timeout":
            self.now += timedelta(seconds=8)  # a slow carrier costs simulated time
            raise httpx.ReadTimeout("sandbox: portal timed out")
        if fault in ("429", "503"):
            self.now += timedelta(seconds=1)
            return httpx.Response(
                int(fault), headers={"Retry-After": "0"}, json={"detail": "sandbox fault"}
            )
        if fault == "malformed":
            return httpx.Response(
                200, content=b"{not json", headers={"content-type": "application/json"}
            )
        weight = Decimal(params["weight_kg"])
        chargeable = max(weight, Decimal(params.get("volume_cbm", "0")) * 167)
        per_kg = {int(b): Decimal(rate) for b, rate in airline["per_kg"].items()}
        freight = chargeable * per_kg[max(b for b in per_kg if b <= chargeable)]
        total = max(Decimal(airline["min"]), freight).quantize(Decimal("0.01"), ROUND_HALF_UP)
        rate_id = (
            "R-" + _digest(self.scenario["id"], self.seed, code, origin, destination, weight)[:10]
        )
        valid_until = self.now + timedelta(seconds=airline.get("valid_s", 900))
        record = {
            "rate_id": rate_id,
            "airline": airline["name"],
            "airline_code": code,
            "origin": origin,
            "destination": destination,
            "weight_kg": str(weight),
            "chargeable_weight_kg": str(chargeable),
            "total": str(total),
            "currency": airline.get("currency", "EUR"),
            "fx_eur_per_usd": airline.get("fx"),
            "transit_days": airline.get("transit"),
            "valid_until": valid_until.isoformat(),
            "dataset_version": self.scenario.get("portal", {}).get("dataset_version", "sandbox"),
        }
        self.rates[rate_id] = {**record, "expires": valid_until}
        return httpx.Response(200, json=record)

    def _book(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        reference, rate = body["team_reference"], self.rates.get(body["rate_id"])
        fault = self._fault("booking")
        if fault == "not_sent":
            raise httpx.ConnectError("sandbox: connection refused")
        if fault == "503":
            return httpx.Response(503, json={"detail": "sandbox fault"})
        self.book_requests[reference] = self.book_requests.get(reference, 0) + 1
        self.book_times.setdefault(reference, self.elapsed())
        booking = self.bookings.get(reference)
        if booking is None:
            if rate is None:
                return httpx.Response(404, json={"detail": "unknown rate_id"})
            if self.now > rate["expires"]:
                return httpx.Response(410, json={"detail": "rate expired"})
            booking = {
                "booking_reference": "BK-" + _digest(reference)[:8],
                "team_reference": reference,
                "rate_id": body["rate_id"],
                "airline": rate["airline"],
                "airline_code": rate["airline_code"],
                "price": rate["total"],
                "currency": rate["currency"],
                "status": "confirmed",
            }
            self.bookings[reference] = booking
        elif booking["rate_id"] != body["rate_id"]:
            return httpx.Response(409, json={"detail": "team_reference used with another rate"})
        if fault == "lost_ack":
            raise httpx.ReadTimeout("sandbox: booked, the answer was lost")
        # As documented: 201 for a new booking, 200 when the same pair is sent again.
        return httpx.Response(201 if self.book_requests[reference] == 1 else 200, json=booking)

    # ---------- model gateway ----------
    def _chat(self, request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][-1]["content"]
        fault = self._fault("model")
        call = {"at_s": self.elapsed(), "fault": fault, "prompt_chars": len(prompt)}
        self.model_calls.append(call)
        if fault == "timeout":
            self.now += timedelta(seconds=5)
            raise httpx.ReadTimeout("sandbox: model timed out")
        if fault == "invalid_json":
            content = "Sure! The origin is FRA and the destination is ORD."
        else:
            fixtures = self.scenario.get("model", {})
            answer = next((value for text, value in fixtures.items() if text in prompt), {})
            content = json.dumps(answer)
        call["completion_chars"] = len(content)
        usage = {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(content) // 4,
            "synthetic": True,  # character counts / 4; not a provider measurement
        }
        return httpx.Response(
            200,
            json={"model": MODEL, "choices": [{"message": {"content": content}}], "usage": usage},
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        route = (request.method, request.url.path)
        self.calls[f"{route[0]} {re.sub(r'/[0-9]+', '/{id}', route[1])}"] += 1
        if route == ("GET", "/v1/mailbox/messages"):
            return self._poll(request)
        if route == ("POST", "/v1/mailbox/messages"):
            return self._send(request)
        if route == ("GET", "/v1/portal/ports"):
            return self._ports()
        if route == ("GET", "/v1/portal/brackets"):
            return self._brackets(request)
        if route == ("GET", "/v1/portal/rates"):
            return self._rate(request)
        attachment = re.fullmatch(r"/v1/mailbox/messages/(\d+)/attachments/(\d+)", route[1])
        if route[0] == "GET" and attachment:
            return self._attachment(int(attachment.group(1)), int(attachment.group(2)))
        if route == ("POST", "/v1/portal/bookings"):
            return self._book(request)
        if route == ("POST", "/v1/gateway/chat/completions"):
            return self._chat(request)
        return httpx.Response(404, json={"detail": f"the sandbox has no {route[0]} {route[1]}"})


def _client(world: World) -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(world.handle))


class SandboxMailbox(LiveMailbox):
    def __init__(self, world: World):
        super().__init__(_client(world))

    def _http(self) -> httpx.Client:
        return self._client  # in-memory transport; there is no socket behind it


class SandboxBooking(PortalBooking):
    def __init__(self, world: World):
        super().__init__(_client(world))

    def _http(self) -> httpx.Client:
        return self._client


class SandboxGateway:
    def __init__(self, world: World):
        self._client = _client(world)

    def complete(self, prompt: str, model: str | None = None) -> dict:
        body = {"model": model or MODEL, "messages": [{"role": "user", "content": prompt}]}
        try:
            data = self._client.post("/v1/gateway/chat/completions", json=body).json()
            text = data["choices"][0]["message"]["content"] or ""
        except (httpx.TransportError, ValueError, KeyError, IndexError) as exc:
            raise GatewayError(f"{type(exc).__name__}: {exc}") from exc
        _record_usage(None, MODEL, simulated=True)
        return {"simulated": True, "text": text, "model": MODEL, "usage": data.get("usage") or {}}


def adapters(world: World) -> dict:
    gateway = SandboxGateway(world)

    def rates() -> PortalClient:
        http = httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(world.handle))
        state = world.portal_state if world.share_portal_state else None
        return PortalClient(http=http, max_retries=1, state=state)

    return {
        "mailbox": SandboxMailbox(world),
        "gateway": gateway,
        "booking": SandboxBooking(world),
        "rates": rates,
        "mail_scope": lambda: [str(m["id"]) for m in world.inbox],
        "extraction_model": {"gateway": gateway, "name": MODEL},
    }
