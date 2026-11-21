"""Drill checks, against the isolated sandbox project only (`make drill-up` first).

1. Kill the worker container with SIGKILL while it sends a first quote, once before the mail
   can arrive and once after the simulator has accepted it. Restart the worker and check what
   the simulator received: never a second copy, and exactly one after the operator's decision.
2. Try to reach the internet from inside the sandbox containers. Every attempt must fail.

Writes reports/drill.json and exits non-zero when a check fails.
"""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

PROJECT = "fiveu-sandbox"
WORKER = f"{PROJECT}-drill-worker-1"
API, SIM = "http://127.0.0.1:8102", "http://127.0.0.1:8101"
OPERATOR = {"X-Operator-Token": "sandbox-operator"}
failures: list[str] = []


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=check, capture_output=True, text=True)


def check(ok: bool, what: str) -> bool:
    print(("  ok    " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        failures.append(what)
    return ok


def wait(condition, seconds: float, what: str):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.3)
    check(False, f"timed out after {seconds:.0f} s waiting until {what}")
    return None


def guard() -> None:
    """Refuse to act on anything but the drill worker of the sandbox project, in sandbox mode."""
    labels = '{{ index .Config.Labels "com.docker.compose.project" }}|'
    labels += '{{ index .Config.Labels "com.docker.compose.service" }}'
    found = docker("inspect", WORKER, "--format", labels, check=False).stdout.strip()
    if found != f"{PROJECT}|drill-worker":
        sys.exit(f"refusing: {WORKER} is not the drill worker of project {PROJECT} ({found!r})")
    status = httpx.get(f"{API}/sandbox/status").json()
    if status["app_mode"] != "sandbox" or status["live_actions_enabled"]:
        sys.exit(f"refusing: the API on {API} is not a sandbox with live actions off")


def sim() -> dict:
    return httpx.get(f"{SIM}/_sim/state").json()


def quotes() -> list[dict]:
    return [mail for mail in sim()["sent"] if "Total:" in mail["body"]]


def sessions() -> set[str]:
    return {session["id"] for session in httpx.get(f"{API}/api/sessions").json()}


def actions(session_id: str) -> list[dict]:
    return httpx.get(f"{API}/api/sessions/{session_id}/actions").json()


def kill_case(mode: str) -> dict:
    name = "kill_before_send" if mode == "before" else "kill_after_acceptance"
    print(f"\n{name}: SIGKILL the worker {mode} the simulator accepts the first quote")
    earlier = sessions()
    hold = {"send": mode, "seconds": 12}
    httpx.post(f"{SIM}/_sim/load", json={"scenario": "happy_path", "hold": hold})
    result: dict = {"case": name}
    if not wait(lambda: sim()["holding"], 40, "the worker is inside the quote send"):
        return result
    docker("kill", WORKER)
    wait(lambda: not sim()["holding"], 30, "the held request is over")
    expected = 0 if mode == "before" else 1
    result["quotes_at_kill"] = len(quotes())
    check(result["quotes_at_kill"] == expected, f"the simulator holds {expected} quote(s)")

    new = sessions() - earlier
    if not check(len(new) == 1, "the request has one session"):
        return result
    session_id = new.pop()
    quote = next((a for a in actions(session_id) if a["purpose"] == "first_quote"), None)
    result["status_at_kill"] = quote["delivery_status"] if quote else None
    if not check(
        result["status_at_kill"] == "sending",
        "the action is 'sending' in the database: that was committed before the call",
    ):
        return result

    def status() -> str:
        return next(a["delivery_status"] for a in actions(session_id) if a["id"] == quote["id"])

    docker("start", WORKER)
    wait(lambda: status() == "UNKNOWN", 90, "the restarted worker marks the stale send UNKNOWN")
    result["status_after_restart"] = status()
    time.sleep(8)
    result["quotes_after_restart"] = len(quotes())
    check(result["quotes_after_restart"] == expected, "the restart alone sent nothing again")

    # The operator looked at the mailbox and records what is true there.
    base = f"{API}/api/sessions/{session_id}"
    httpx.post(f"{base}/pause", headers=OPERATOR).raise_for_status()
    answer = {"sent": mode == "after"}
    resolve = f"{base}/actions/{quote['id']}/resolve"
    httpx.post(resolve, json=answer, headers=OPERATOR).raise_for_status()
    httpx.post(f"{base}/resume", headers=OPERATOR).raise_for_status()
    wait(lambda: status() == "confirmed" and len(quotes()) == 1, 60, "the quote is delivered once")
    time.sleep(8)
    result["quotes_final"], result["status_final"] = len(quotes()), status()
    check(result["quotes_final"] == 1, "exactly one quote reached the customer in the end")
    wait(lambda: sim()["bookings"] == 1, 120, "the customer accepted and the shipment is booked")
    state = sim()
    result["customer"], result["bookings"] = state["requests"]["rfq1"]["outcome"], state["bookings"]
    check(result["customer"] == "accepted", "the customer accepted the one quote")
    check(result["bookings"] == 1, "one booking exists")
    return result


def second_worker() -> dict:
    """The first rollout runs one worker. A second one must stop instead of sharing the
    mailbox, the send budget and the portal quota."""
    print("\none worker: start a second drill worker next to the running one")
    root = Path(__file__).resolve().parents[2]
    compose = ["compose", "-f", str(root / "compose.sandbox.yaml"), "--project-directory"]
    run = ["--profile", "drill", "run", "--rm", "--no-deps", "-T", "drill-worker"]
    done = docker(*compose, str(root), *run, check=False)
    said = "another worker already holds the worker lock" in done.stdout + done.stderr
    check(done.returncode != 0 and said, "the second worker refuses to start")
    return {"exit_code": done.returncode, "refused": said}


def isolation() -> list[dict]:
    """Attempts that must be refused. The first two use a fresh interpreter, so they test the
    container's network, not the application's own socket guard."""
    print("\nisolation: outbound attempts from inside the sandbox containers")
    attempts = {
        "TCP to 1.1.1.1:443": "import socket; socket.create_connection(('1.1.1.1', 443), 4)",
        "HTTPS to example.com": (
            "import urllib.request; urllib.request.urlopen('https://example.com', timeout=4)"
        ),
        "the app's socket guard": (
            "from app.core import netguard; netguard.install('postgresql://u@db/x');"
            "import socket; socket.getaddrinfo('example.com', 443)"
        ),
        "sandbox settings with a live URL": (
            "import os; os.environ['HACKATHON_API_BASE']='https://live.example';"
            "from app.core.config import Settings; Settings(_env_file=None)"
        ),
    }
    rows = []
    for service in ("api", "worker", "drill-api", "drill-worker", "sim"):
        container = f"{PROJECT}-{service}-1"
        for attempt, code in attempts.items():
            done = docker("exec", container, "python", "-c", code, check=False)
            why = (done.stderr.strip().splitlines() or ["no error"])[-1][:140]
            refused = done.returncode != 0
            check(refused, f"{service}: {attempt} is refused ({why})")
            rows.append({"container": container, "attempt": attempt, "refused": refused})
    return rows


def main() -> int:
    guard()
    report = {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data": "SIMULATED",
        "cases": [kill_case("before"), kill_case("after")],
        "second_worker": second_worker(),
        "isolation": isolation(),
        "failures": failures,
    }
    out = Path(__file__).resolve().parents[2] / "reports" / "drill.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"\n{'FAILED' if failures else 'passed'}: {len(failures)} failure(s). Report: {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
