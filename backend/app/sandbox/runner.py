"""Run a scenario against the real router, pricing and workflow code, on simulated time.

    python -m app.sandbox.runner --scenario happy_path --seed 42
    python -m app.sandbox.runner --scenario all --out ../reports

The same scenario and seed give the same logical result (the `digest`). Results describe this
local simulator and its local customer rules. They are not a competition score.
"""

import argparse
import hashlib
import json
import platform as host
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

import app.platform as registry
from app.core import clock, netguard
from app.core.config import get_settings
from app.core.errors import DomainError
from app.crm.queries import pause_session, resume_session
from app.db import repositories
from app.db.models import ActionIntentRow, JobRow, MessageRow, RateCandidateRow
from app.db.session import get_session_factory
from app.platform.mailbox import (
    CURSOR_NAME,
    MailboxError,
    ingest_message,
    map_poll_item,
    poll_mailbox_once,
)
from app.platform.outbox import deliver_pending, expire_stale_sends
from app.platform.router import route_message
from app.sandbox.scenarios import SCENARIOS
from app.sandbox.world import _PRICE, World, adapters
from app.workflows.booking import retry_open_bookings
from app.workflows.rfq import close_overdue_sourcing

RETRYABLE = {
    "session_paused",
    "send_failed",
    "send_in_progress",
    "send_throttled",
    "concurrent_conflict",
}
MAX_ATTEMPTS = 80
_lock = threading.Lock()
_STATUS = re.compile(r"update for booking \S+ is: (.+?) \(reported")


def run(scenario_id: str, seed: int | None = None, until_s: int | None = None) -> dict:
    scenario = SCENARIOS[scenario_id]
    seed = get_settings().simulation_seed if seed is None else seed
    factory = get_session_factory()
    with _lock:  # adapters and clock are process-wide, so one scenario at a time
        with factory() as db:
            base = repositories.get_cursor(db, CURSOR_NAME)
        world = World(scenario, seed, base)
        registry.set_adapters(adapters(world))
        clock.set_clock(world.tick)
        started = time.perf_counter()
        try:
            state = _drive(world, factory, until_s)
        finally:
            registry.set_adapters(None)
            clock.set_clock(None)
        return _report(world, factory, state, time.perf_counter() - started)


def _drive(world: World, factory, until_s: int | None = None) -> dict:
    mailbox = registry.get_adapters()["mailbox"]
    state = {"todo": [], "waiting": [], "errors": [], "duplicates": 0, "special": set()}
    actions = sorted(world.scenario.get("actions", []), key=lambda a: a["at"])
    for _ in range(world.scenario.get("max_steps", 600)):
        state["waiting"] += [str(item["id"]) for item in world.deliver_due()]
        _ingest(world, factory, mailbox, state)
        for entry in list(state["todo"]):
            _route(world, factory, entry, state)
        while actions and actions[0]["at"] <= world.elapsed():
            _act(world, factory, actions.pop(0))
        with factory() as db:
            roots = [str(r["root"]) for r in world.requests.values()]
            retry_open_bookings(db)
            expire_stale_sends(db)
            deliver_pending(db, mailbox, roots)
            waiting = close_overdue_sourcing(db)
            db.commit()
            sending = db.scalars(
                select(ActionIntentRow.id).where(
                    ActionIntentRow.delivery_status.in_(("sending", "pending")),
                    ActionIntentRow.payload.has_key("body"),  # noqa: W601 - JSONB key test
                    ActionIntentRow.kind != "booking",
                    ActionIntentRow.payload["thread_id"].astext.in_(roots),
                )
            ).first()
        # Keep simulated time moving until every send of this run has a recorded outcome.
        busy = world.queue or state["todo"] or state["waiting"] or actions or sending or waiting
        if not busy:
            break
        stops = [actions[0]["at"]] if actions else []
        stops += [until_s] if until_s is not None else []
        world.advance(world.start + timedelta(seconds=min(stops)) if stops else None)
        if until_s is not None and world.elapsed() >= until_s:
            break  # stopped part-way on request: mail due from here on is not delivered
    return state


def _ingest(world: World, factory, mailbox, state: dict) -> None:
    with factory() as db:
        try:
            while poll_mailbox_once(db, mailbox):
                pass
        except MailboxError as exc:
            state["errors"].append({"where": "poll", "code": exc.kind, "at_s": world.elapsed()})
        for external in state["waiting"]:
            if world.specs[int(external)].get("duplicate") and external not in state["special"]:
                state["special"].add(external)
                item = next(m for m in world.inbox if str(m["id"]) == external)
                _, created = ingest_message(db, map_poll_item(item))  # the same mail again
                state["duplicates"] += 0 if created else 1
        rows = db.scalars(
            select(MessageRow).where(MessageRow.external_id.in_(state["waiting"]))
        ).all()
        stored = {m.external_id: m.id for m in rows}
        # This run routes its own mail. Close the queue entries so no other worker takes them.
        ids = [str(v) for v in stored.values()]
        for job in db.scalars(select(JobRow).where(JobRow.payload["message_id"].astext.in_(ids))):
            job.status = "done"
        db.commit()
    for external in [e for e in state["waiting"] if e in stored]:
        state["todo"].append([external, stored[external], 0])
        state["waiting"].remove(external)


def _attempt(factory, message_id) -> str | None:
    with factory() as db:
        try:
            route_message(db, message_id)
            db.commit()
            return None
        except DomainError as exc:
            db.commit()  # like the worker: what the handler recorded before failing is kept
            return exc.code
        except IntegrityError:
            db.rollback()
            return "concurrent_conflict"


def _route(world: World, factory, entry: list, state: dict) -> None:
    external, message_id, _ = entry
    spec = world.specs[int(external)]
    first = external not in state["special"] or spec.get("duplicate")
    if spec.get("two_workers") and first and f"race{external}" not in state["special"]:
        state["special"].add(f"race{external}")
        codes: list = []
        workers = [
            threading.Thread(target=lambda: codes.append(_attempt(factory, message_id)))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        code = next((c for c in codes if c in RETRYABLE), next((c for c in codes if c), None))
    elif spec.get("crash") and f"crash{external}" not in state["special"]:
        state["special"].add(f"crash{external}")
        with factory() as db:
            try:
                route_message(db, message_id)
            except DomainError:
                pass
            db.rollback()  # the worker dies here; everything since its last commit is lost
        code = _attempt(factory, message_id)  # the job is picked up again
    else:
        code = _attempt(factory, message_id)
    entry[2] += 1
    if code in RETRYABLE and entry[2] < MAX_ATTEMPTS:
        return
    state["todo"].remove(entry)
    if code:
        key = world.keys[int(external)]
        state["errors"].append({"mail": key, "code": code, "at_s": world.elapsed()})


def _act(world: World, factory, action: dict) -> None:
    with factory() as db:
        root = world.requests[action["request"]]["root"]
        message = db.scalars(select(MessageRow).where(MessageRow.external_id == str(root))).one()
        session = repositories.session_by_thread(db, message.thread_id)
        (pause_session if action["do"] == "pause" else resume_session)(db, session.id)
        db.commit()


def _get(data, path: str):
    for part in path.split("."):
        data = data.get(part) if isinstance(data, dict) else None
    return data


def _matches(actual, expected) -> bool:
    if isinstance(expected, tuple) and expected[0] == ">=":
        return actual is not None and actual >= expected[1]
    return actual == expected


def _report(world: World, factory, state: dict, wall: float) -> dict:
    scenario, requests, threads, totals = world.scenario, {}, {}, Counter()
    reasons: list[str] = []
    secret = get_settings().hackathon_api_key
    with factory() as db:
        inbox_ids = [str(m["id"]) for m in world.inbox]
        stored = db.scalars(select(MessageRow).where(MessageRow.external_id.in_(inbox_ids))).all()
        totals["mails_stored"] = len(stored)
        tariff_mail = [m.raw for m in stored if isinstance(m.raw, dict) and "tariff" in m.raw]
        for key, req in world.requests.items():
            message = db.scalars(
                select(MessageRow).where(MessageRow.external_id == str(req["root"]))
            ).first()
            rfq = repositories.rfq_by_message(db, message.id) if message else None
            session = repositories.session_by_rfq(db, rfq.id) if rfq else None
            intents = (
                db.scalars(select(ActionIntentRow).where(ActionIntentRow.rfq_id == rfq.id)).all()
                if rfq
                else []
            )
            by_body = {i.payload.get("body"): i for i in intents if i.payload.get("body")}
            sent = [s for s in world.sent if world.threads.get(s["id"]) == key]
            purposes = [
                by_body[s["body"]].payload["purpose"] if s["body"] in by_body else "unrecorded"
                for s in sent
            ]
            events = repositories.events_for(db, session.id) if session else []
            extracted = next((e.payload for e in reversed(events) if e.type == "rfq_extracted"), {})
            quote = repositories.quote_for(db, rfq.id) if rfq else None
            candidate = db.get(RateCandidateRow, quote.candidate_id) if quote else None
            booking = repositories.booking_for(db, rfq.id) if rfq else None
            statuses = [m.group(1) for a in req["tracking_answers"] if (m := _STATUS.search(a))]
            priced = req["priced"]
            deadline_s = int((req["deadline"] - world.start).total_seconds())
            requests[key] = {
                "stage": session.stage if session else None,
                "blocked_on": session.exception if session else None,
                "rfq_status": rfq.status if rfq else None,
                "extracted": {
                    "origin": extracted.get("origin"),
                    "destination": extracted.get("destination"),
                    "weight_kg": extracted.get("weight_kg"),
                    "method": extracted.get("method"),
                },
                "dangerous_goods": (extracted.get("dangerous_goods") or {}).get("status"),
                "quote": {
                    "carrier": candidate.carrier,
                    "total": str(quote.total),
                    "currency": quote.currency,
                    "transit_days": candidate.transit_days,
                    "source": candidate.source_ref.split(":")[0],
                }
                if quote
                else None,
                "quote_latency_s": priced[0]["at_s"] - req["delivered_s"] if priced else None,
                "outbound": purposes,
                "deliveries": [
                    by_body[s["body"]].delivery_status for s in sent if s["body"] in by_body
                ],
                "booking": {
                    "status": booking.status,
                    "portal_requests": world.book_requests.get(booking.team_reference, 0),
                    "first_request_s": world.book_times.get(booking.team_reference),
                }
                if booking
                else None,
                "customer": {
                    "outcome": req["outcome"],
                    "reason": req["reason"],
                    "priced_messages": len(priced),
                    "questions": req["questions"],
                    "irrelevant_questions": req["irrelevant_questions"],
                    "tracking_mentions": statuses[0] if statuses else None,
                },
                "events": [e.type for e in events],
            }
            threads[key] = sorted(
                [
                    {
                        "direction": "in",
                        "at_s": req["delivered_s"],
                        "subject": m["subject"],
                        "body": m["body"],
                        "attachments": [a.get("filename") for a in m["attachments"]],
                    }
                    for m in world.inbox
                    if world.threads.get(m["id"]) == key
                ]
                + [
                    {
                        "direction": "out",
                        "at_s": s["at_s"],
                        "subject": s["subject"],
                        "body": s["body"],
                        "purpose": p,
                    }
                    for s, p in zip(sent, purposes, strict=True)
                ],
                key=lambda m: (m["at_s"], m["direction"]),
            )
            totals["received"] += 1
            totals["answered_in_time"] += bool(sent and sent[0]["at_s"] <= deadline_s)
            totals["unanswered"] += not sent
            totals["quoted"] += quote is not None
            totals[req["outcome"] or "no_decision"] += 1
            totals["expired"] += bool(rfq and rfq.status == "expired")
            totals["booked"] += bool(booking and booking.status == "confirmed")
            totals["bookings_unknown"] += bool(booking and booking.status == "UNKNOWN")
            totals["duplicate_quotes"] += max(0, len(priced) - 1)
            totals["priced_after_deadline"] += sum(1 for p in priced if p["at_s"] > deadline_s)
            totals["tracking_answers"] += len(req["tracking_answers"])
            totals["unknown_sends"] += sum(
                1 for i in intents if i.delivery_status == "UNKNOWN" and i.kind != "booking"
            )
            totals["price_in_non_quote"] += sum(
                1
                for s, p in zip(sent, purposes, strict=True)
                if p not in {"first_quote", "booking_confirmed"} and _PRICE.search(s["body"])
            )
            totals["wrong_recipients"] += sum(1 for s in sent if s["to"] != req["from"])
            if req["reason"]:
                reasons.append(req["reason"])
    totals.update(
        stray_outbound=len(world.stray),
        carrier_mails_sent=len(world.carrier_mail),
        tariffs_for_review=sum(
            1 for m in tariff_mail if m["tariff"]["status"] == "review_required"
        ),
        secrets_in_outbound=sum(
            1
            for s in world.sent
            if "x-api-key" in s["body"].lower() or (secret and secret in s["body"])
        ),
        bookings_created=len(world.bookings),
        duplicate_inbound_ignored=state["duplicates"],
        polls_with_mail=world.polls_with_mail,
        largest_poll_page=world.max_page,
        mails_delivered=len(world.inbox),
        faults_injected=len(world.fault_log),
        model_calls=len(world.model_calls),
    )
    for name in ("accepted", "rejected", "no_offer", "no_decision"):
        totals.setdefault(name, 0)
    totals = dict(sorted(totals.items()))
    checks = []
    for key, wanted in scenario["expect"].items():
        source = totals if key == "_totals" else requests.get(key, {})
        for path, expected in wanted.items():
            actual = _get(source, path)
            checks.append(
                {
                    "check": f"{key}.{path}",
                    "expected": expected,
                    "actual": actual,
                    "passed": _matches(actual, expected),
                }
            )
    for name in (
        "duplicate_quotes",
        "price_in_non_quote",
        "priced_after_deadline",
        "stray_outbound",
        "secrets_in_outbound",
        "wrong_recipients",
    ):
        if not any(c["check"] == f"_totals.{name}" for c in checks):
            checks.append(
                {
                    "check": f"always.{name}",
                    "expected": 0,
                    "actual": totals.get(name, 0),
                    "passed": totals.get(name, 0) == 0,
                }
            )
    logical = json.dumps({"requests": requests, "totals": totals}, sort_keys=True, default=str)
    return {
        "scenario": scenario["id"],
        "version": scenario["version"],
        "title": scenario.get("title", ""),
        "seed": world.seed,
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "requests": requests,
        "totals": totals,
        "rejection_reasons": reasons,
        "digest": hashlib.sha256(logical.encode()).hexdigest(),
        "threads": threads,
        "faults": world.fault_log,
        "errors": state["errors"],
        "simulated_seconds": world.elapsed(),
        "wall_seconds": round(wall, 3),
        "model_usage": {
            "calls": len(world.model_calls),
            "synthetic": True,
            "approx_tokens": sum(
                (c["prompt_chars"] + c.get("completion_chars", 0)) // 4 for c in world.model_calls
            ),
            "measured_cost": None,
        },
        "blocked_network_attempts": len(netguard.blocked),
        "data": "SIMULATED",
    }


def _git() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
        return f"{head}{' + uncommitted changes' if dirty else ''}"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def write_report(results: list[dict], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    meta = {
        "working_tree": _git(),
        "python": sys.version.split()[0],
        "platform": host.platform(),
        "packages": {p: version(p) for p in ("fastapi", "sqlalchemy", "httpx", "pydantic")},
        "app_mode": settings.app_mode,
        "live_actions_enabled": settings.live_actions_enabled,
        "llm_mode": settings.llm_mode,
        "note": "Local simulator results with local customer rules. Not a competition score.",
    }
    (out / "sandbox-results.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2, default=str) + "\n"
    )
    total: Counter = Counter()
    for result in results:  # a per-run maximum is not a count, so it is not summed
        total.update({k: v for k, v in result["totals"].items() if k != "largest_poll_page"})
    lines = [
        "# Sandbox report",
        "",
        "Everything here is SIMULATED data from the local sandbox. The customers follow local "
        "rules written in `backend/app/sandbox/scenarios.py`; they are not the organiser's "
        "evaluator, and none of this is a leaderboard result.",
        "",
        f"- Working tree: `{meta['working_tree']}`",
        f"- Python {meta['python']} on {meta['platform']}; "
        + ", ".join(f"{k} {v}" for k, v in meta["packages"].items()),
        f"- APP_MODE={meta['app_mode']}, LIVE_ACTIONS_ENABLED={meta['live_actions_enabled']}, "
        f"LLM_MODE={meta['llm_mode']}",
        f"- Outbound network attempts blocked by the guard: "
        f"{sum(r['blocked_network_attempts'] for r in results[-1:])}",
        "",
        "## Scenarios",
        "",
        "| Scenario | Version | Seed | Result | Simulated s | Wall s | Digest |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        lines.append(
            f"| `{r['scenario']}` | {r['version']} | {r['seed']} | "
            f"{'PASS' if r['passed'] else 'FAIL'} | {r['simulated_seconds']} | "
            f"{r['wall_seconds']} | `{r['digest'][:12]}` |"
        )
    failed = [(r["scenario"], c) for r in results for c in r["checks"] if not c["passed"]]
    lines += ["", "## Failed checks", ""]
    lines += [
        f"- `{name}` {c['check']}: expected `{c['expected']}`, got `{c['actual']}`"
        for name, c in failed
    ] or ["None."]
    lines += ["", "## Counts across all scenarios (simulated)", ""]
    lines += [f"- {name.replace('_', ' ')}: {count}" for name, count in sorted(total.items())]
    lines += ["", "## Rejection reasons (local customer rules)", ""]
    lines += [f"- {reason}" for r in results for reason in r["rejection_reasons"]] or ["None."]
    lines += ["", "## Injected faults", ""]
    lines += [
        f"- `{r['scenario']}` at {f['at_s']} s: {f['fault']} on {f['where']}"
        for r in results
        for f in r["faults"]
    ] or ["None."]
    lines += ["", "## Price checks against hand-worked expectations", ""]
    lines += [
        f"- `{r['scenario']}` {c['check']}: expected {c['expected']}, got {c['actual']} "
        f"({'match' if c['passed'] else 'MISMATCH'})"
        for r in results
        for c in r["checks"]
        if c["check"].endswith("quote.total")
    ]
    lines += ["", "## Time and model usage", ""]
    lines += [
        f"- Simulated business time: {sum(r['simulated_seconds'] for r in results)} s",
        f"- Actual local run time: {sum(r['wall_seconds'] for r in results):.1f} s",
        f"- Synthetic model calls: {sum(r['model_usage']['calls'] for r in results)} "
        "(fake model; token counts are character estimates)",
        "- Measured real model usage and cost: none (no real model was called)",
        "",
        "## Customer reply samples (for human review)",
        "",
    ]
    seen: set[str] = set()
    for r in results:
        for thread in r["threads"].values():
            for mail in thread:
                purpose = mail.get("purpose")
                if mail["direction"] == "out" and purpose not in seen:
                    seen.add(purpose)
                    lines += [
                        f"### {purpose} (from `{r['scenario']}`)",
                        "",
                        "```text",
                        mail["body"],
                        "```",
                        "",
                    ]
    (out / "sandbox-report.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sandbox scenarios.")
    parser.add_argument("--scenario", default="all")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None, help="directory for the report files")
    parser.add_argument("--json", action="store_true", help="print full results as JSON")
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.app_mode == "sandbox":
        netguard.install(settings.database_url)
    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenario {unknown[0]}; choose from: {', '.join(SCENARIOS)}")
    results = [run(name, args.seed) for name in names]
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print(
                f"{'PASS' if r['passed'] else 'FAIL'}  {r['scenario']:<22} seed={r['seed']} "
                f"sim={r['simulated_seconds']}s wall={r['wall_seconds']}s {r['digest'][:12]}"
            )
            for c in r["checks"]:
                if not c["passed"]:
                    print(f"      {c['check']}: expected {c['expected']!r}, got {c['actual']!r}")
            for e in r["errors"]:
                print(f"      note: {e}")
    if args.out:
        write_report(results, args.out)
        print(f"report: {args.out / 'sandbox-report.md'}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
