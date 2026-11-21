"""The simulator as a process of its own, on real time.

The drill stack (`make drill-up`) points a real API and a real worker at this server. Killing a
container or clicking in the operator UI can then be checked against what "the outside"
received, which no in-process test can show. It serves the same World as the scenario runner.
"""

import json
import threading
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.sandbox.scenarios import SCENARIOS
from app.sandbox.world import World

app = FastAPI(title="5U simulator (sandbox)")
_lock = threading.Lock()
_state: dict = {"world": None, "hold": None, "holding": False}


class Load(BaseModel):
    scenario: str
    seed: int = 42
    # Stall the next outbound mail whose body contains `match`:
    #   {"send": "before", "seconds": 12}  the request is held and then never arrives
    #   {"send": "after",  "seconds": 12}  the mail is accepted, the answer is held back
    hold: dict | None = None
    reply_delay: int | None = None  # seconds every simulated customer waits before answering


@app.post("/_sim/load")
def load(body: Load):
    if body.scenario not in SCENARIOS:
        raise HTTPException(404, f"no scenario {body.scenario}")
    with _lock:
        now = datetime.now(UTC)
        previous = _state["world"]
        # Mail ids only ever rise, also across loads: the worker keeps its mailbox cursor.
        base = max(int(now.timestamp()), previous._id if previous else 0)
        scenario = {**SCENARIOS[body.scenario], "clock": now.isoformat()}
        if body.reply_delay is not None:
            slow = {"reply_delay": body.reply_delay}
            scenario["mails"] = [
                {**mail, "customer": {**mail["customer"], **slow}} if "customer" in mail else mail
                for mail in scenario["mails"]
            ]
        _state.update(world=World(scenario, body.seed, base), hold=body.hold, holding=False)
    return {"loaded": body.scenario, "first_id": base + 1}


@app.get("/_sim/state")
def state():
    world = _state["world"]
    if world is None:
        return {"loaded": None}
    with _lock:
        return {
            "loaded": world.scenario["id"],
            "holding": _state["holding"],
            "sent": list(world.sent),
            "carrier_mail": len(world.carrier_mail),
            "stray": len(world.stray),
            "bookings": len(world.bookings),
            "calls": dict(world.calls),
            "requests": {
                key: {
                    "root": request["root"],
                    "outcome": request["outcome"],
                    "reason": request["reason"],
                    "priced": request["priced"],
                    "questions": request["questions"],
                }
                for key, request in world.requests.items()
            },
        }


def _serve(method: str, url: str, content: bytes) -> httpx.Response:
    world, hold = _state["world"], _state["hold"]
    if world is None:
        return httpx.Response(503, json={"detail": "no scenario is loaded"})
    stall = None
    if hold and (method, urlsplit(url).path) == ("POST", "/v1/mailbox/messages"):
        if hold.get("match", "Total:") in (json.loads(content).get("body") or ""):
            stall, _state["hold"] = hold, None
    if stall and stall["send"] == "before":
        _pause(stall["seconds"])
        return httpx.Response(503, json={"detail": "sandbox: this request never arrived"})
    with _lock:
        world.now = datetime.now(UTC)
        world.deliver_due()
        try:
            response = world.handle(httpx.Request(method, url, content=content))
        except httpx.TransportError as exc:  # an injected fault; over HTTP it is a gateway error
            response = httpx.Response(504, json={"detail": str(exc)})
    if stall:
        _pause(stall["seconds"])  # the mail is recorded; only the acknowledgement is late
    return response


def _pause(seconds: float) -> None:
    _state["holding"] = True
    time.sleep(seconds)
    _state["holding"] = False


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT"])
async def simulated(request: Request):
    answer = await run_in_threadpool(_serve, request.method, str(request.url), await request.body())
    keep = {k: v for k, v in answer.headers.items() if k.lower() in {"content-type", "retry-after"}}
    return Response(answer.content, answer.status_code, keep)
