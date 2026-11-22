import { useCallback, useEffect, useId, useState } from "react";
import {
  listSandboxScenarios,
  runSandboxScenario,
  type SandboxResult,
  type SandboxScenario,
} from "../../api/client";
import { OperatorToken } from "../review/OperatorToken";
import { RunResult } from "./RunResult";
import "./sandbox.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";
const statusOf = (message: string) => Number(message.match(/^(\d{3})\b/)?.[1] ?? 0);
const messageOf = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

type ListState =
  | { kind: "loading" }
  | { kind: "off" }
  | { kind: "error"; message: string }
  | { kind: "ready"; scenarios: SandboxScenario[] };

export function SandboxConsole() {
  const [list, setList] = useState<ListState>({ kind: "loading" });
  const [scenario, setScenario] = useState("happy_path");
  const [seed, setSeed] = useState(42);
  const [until, setUntil] = useState("");
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [needsToken, setNeedsToken] = useState(false);
  const ids = { scenario: useId(), seed: useId(), until: useId() };

  const load = useCallback(async () => {
    setList({ kind: "loading" });
    try {
      const scenarios = await listSandboxScenarios();
      setList({ kind: "ready", scenarios });
      setScenario((current) =>
        scenarios.some((item) => item.id === current) ? current : (scenarios[0]?.id ?? current),
      );
    } catch (cause) {
      const message = messageOf(cause);
      setList(statusOf(message) === 404 ? { kind: "off" } : { kind: "error", message });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const run = async () => {
    setBusy(true);
    setRunError(null);
    try {
      setResult(await runSandboxScenario(scenario, seed, until === "" ? null : Number(until)));
      setNeedsToken(false);
    } catch (cause) {
      const message = messageOf(cause);
      if (statusOf(message) === 401) {
        setNeedsToken(true);
        setRunError("The sandbox API asked for an operator token. Enter it above and run again.");
      } else {
        setRunError(message);
      }
    } finally {
      setBusy(false);
    }
  };

  const scenarios = list.kind === "ready" ? list.scenarios : [];
  const chosen = scenarios.find((item) => item.id === scenario);

  return (
    <div className="page sb">
      <header className="page-head">
        <div>
          <h1 className="display">Sandbox</h1>
          <p>
            Replay a scripted scenario against the real agents in a closed world. Simulated data:
            nothing sends a real email, books a real shipment or calls a real model.
          </p>
        </div>
        <div className="sb-head-side">
          <span className="chip chip-warning">Simulated data</span>
          {list.kind === "ready" && <OperatorToken needed={needsToken} />}
        </div>
      </header>

      {list.kind === "loading" && (
        <div className="sb-panel card" aria-busy="true" aria-label="Loading scenarios">
          <div className="skeleton sb-skel-line" />
          <div className="skeleton sb-skel-line sb-skel-short" />
        </div>
      )}

      {list.kind === "off" && <SandboxOff />}

      {list.kind === "error" && (
        <div className="sb-panel card">
          <div className="error-box" role="alert">
            Could not reach the sandbox API at <span className="mono">{API_URL}</span>.{" "}
            {list.message}
          </div>
          <button className="btn sb-retry" type="button" onClick={() => void load()}>
            Try again
          </button>
        </div>
      )}

      {list.kind === "ready" && (
        <>
          <form
            className="sb-panel card"
            onSubmit={(event) => {
              event.preventDefault();
              void run();
            }}
          >
            <div className="sb-form">
              <div className="sb-field sb-field-wide">
                <label htmlFor={ids.scenario}>Scenario</label>
                <select
                  id={ids.scenario}
                  className="select"
                  value={scenario}
                  onChange={(event) => setScenario(event.target.value)}
                >
                  {scenarios.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.id}
                    </option>
                  ))}
                </select>
              </div>
              <div className="sb-field">
                <label htmlFor={ids.seed}>Seed</label>
                <input
                  id={ids.seed}
                  className="input num"
                  type="number"
                  value={seed}
                  onChange={(event) => setSeed(Number(event.target.value))}
                />
              </div>
              <div className="sb-field">
                <label htmlFor={ids.until}>Stop at second</label>
                <input
                  id={ids.until}
                  className="input num"
                  type="number"
                  min={0}
                  placeholder="Run to end"
                  value={until}
                  onChange={(event) => setUntil(event.target.value)}
                />
              </div>
              <button
                className="btn btn-accent sb-run"
                type="submit"
                disabled={busy || scenarios.length === 0}
              >
                {busy ? "Running…" : "Run scenario"}
              </button>
            </div>
            {chosen && (
              <p className="sb-chosen">
                <span className="subtle">v{chosen.version}</span> {chosen.title || chosen.id}
              </p>
            )}
            {scenarios.length === 0 && (
              <p className="muted">The sandbox API lists no scenarios.</p>
            )}
            {runError && (
              <p className="error-box sb-run-error" role="alert">
                {runError}
              </p>
            )}
          </form>

          <div aria-live="polite" aria-busy={busy}>
            {busy && !result && (
              <div className="sb-panel card">
                <div className="skeleton sb-skel-hero" />
              </div>
            )}
            {result && <RunResult result={result} stale={busy} />}
            {!result && !busy && (
              <p className="empty">Pick a scenario and run it. The verdict, checks and mail threads appear here.</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function SandboxOff() {
  return (
    <section className="sb-off card card-tint" aria-labelledby="sb-off-h">
      <span className="chip">
        <span className="dot" aria-hidden="true" /> 404 from /sandbox/scenarios
      </span>
      <h2 id="sb-off-h" className="sb-off-title">
        Sandbox is off.
      </h2>
      <p className="muted">
        This console only works when the API runs with <span className="mono">APP_MODE=sandbox</span>.
        The API at <span className="mono">{API_URL}</span> runs in another mode, so its sandbox
        routes are not registered.
      </p>
      <ol className="sb-steps">
        <li>
          <span className="sb-step-label">Start the sandbox stack</span>
          <code className="sb-cmd">make sandbox-up</code>
          <span className="subtle">
            Uses <span className="mono">compose.sandbox.yaml</span> (sets{" "}
            <span className="mono">APP_MODE: sandbox</span>), API on 127.0.0.1:8100.
          </span>
        </li>
        <li>
          <span className="sb-step-label">Start this frontend against it</span>
          <code className="sb-cmd">make sandbox-ui</code>
          <span className="subtle">
            Then open <span className="mono">http://127.0.0.1:5174/sandbox</span>.
          </span>
        </li>
      </ol>
      <p className="subtle sb-off-foot">
        See the Makefile and <span className="mono">docs/sandbox.md</span>.
        Check the mode with <span className="mono">make sandbox-check</span>.
      </p>
    </section>
  );
}
