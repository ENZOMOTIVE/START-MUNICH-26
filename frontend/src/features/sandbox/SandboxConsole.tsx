import { useEffect, useState } from "react";
import {
  listSandboxScenarios,
  runSandboxScenario,
  type SandboxResult,
  type SandboxScenario,
} from "../../api/client";

const show = (value: unknown) =>
  value === null || value === undefined ? "none" : typeof value === "string" ? value : JSON.stringify(value);

export function SandboxConsole() {
  const [scenarios, setScenarios] = useState<SandboxScenario[]>([]);
  const [scenario, setScenario] = useState("happy_path");
  const [seed, setSeed] = useState(42);
  const [result, setResult] = useState<SandboxResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    listSandboxScenarios()
      .then(setScenarios)
      .catch(() => setError("Sandbox controls are only available when the API runs in sandbox mode."));
  }, []);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      setResult(await runSandboxScenario(scenario, seed));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const chosen = scenarios.find((item) => item.id === scenario);

  return (
    <section className="sandbox" aria-labelledby="sandbox-title">
      <h2 id="sandbox-title">Sandbox console</h2>
      <p className="sandbox-note">
        SANDBOX · SIMULATED DATA. Customers follow local test rules, not the organiser&apos;s
        evaluator. Nothing here sends a real email, books a real shipment or calls a real model.
      </p>
      {error && (
        <p className="sandbox-error" role="alert">
          {error}
        </p>
      )}
      <form
        className="sandbox-form"
        onSubmit={(event) => {
          event.preventDefault();
          void run();
        }}
      >
        <label>
          Scenario
          <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
            {scenarios.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          Seed
          <input
            type="number"
            value={seed}
            onChange={(event) => setSeed(Number(event.target.value))}
          />
        </label>
        <button className="btn btn-primary" type="submit" disabled={busy || scenarios.length === 0}>
          {busy ? "Running…" : "Run scenario"}
        </button>
      </form>
      {chosen && <p className="sandbox-title">{chosen.title}</p>}

      {result && (
        <div className="sandbox-result" aria-live="polite">
          <p>
            <span className={`pill ${result.passed ? "booked" : "exception"}`}>
              {result.passed ? "pass" : "fail"}
            </span>{" "}
            {result.scenario} · seed {result.seed} · {result.simulated_seconds} s simulated ·{" "}
            {result.wall_seconds} s real · digest {result.digest.slice(0, 12)}
          </p>

          <h3>Checks</h3>
          <table className="sandbox-table">
            <thead>
              <tr>
                <th scope="col">Check</th>
                <th scope="col">Expected</th>
                <th scope="col">Got</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {result.checks.map((check) => (
                <tr key={check.check}>
                  <td>{check.check}</td>
                  <td>{show(check.expected)}</td>
                  <td>{show(check.actual)}</td>
                  <td>{check.passed ? "pass" : "FAIL"}</td>
                </tr>
              ))}
            </tbody>
          </table>

          {result.faults.length > 0 && (
            <>
              <h3>Injected faults</h3>
              <ul>
                {result.faults.map((fault, index) => (
                  <li key={index}>
                    {fault.at_s} s: {fault.fault} on {fault.where}
                  </li>
                ))}
              </ul>
            </>
          )}

          {Object.entries(result.threads).map(([key, mails]) => {
            const request = result.requests[key] ?? {};
            return (
              <article key={key} className="sandbox-thread">
                <h3>Request {key}</h3>
                <dl className="sandbox-facts">
                  {["stage", "blocked_on", "extracted", "dangerous_goods", "quote", "booking", "customer"].map(
                    (field) => (
                      <div key={field}>
                        <dt>{field.replace("_", " ")}</dt>
                        <dd>{show(request[field])}</dd>
                      </div>
                    ),
                  )}
                  <div>
                    <dt>state transitions</dt>
                    <dd>{show((request.events as string[] | undefined)?.join(" → "))}</dd>
                  </div>
                </dl>
                <ol className="sandbox-mails">
                  {mails.map((mail, index) => (
                    <li key={index} className={`sandbox-mail ${mail.direction}`}>
                      <p className="sandbox-mail-head">
                        {mail.direction === "in" ? "Received" : `Sent (${mail.purpose})`} at {mail.at_s} s
                        · {mail.subject}
                        {mail.attachments && mail.attachments.length > 0
                          ? ` · attachments: ${mail.attachments.join(", ")}`
                          : ""}
                      </p>
                      <pre>{mail.body}</pre>
                    </li>
                  ))}
                </ol>
              </article>
            );
          })}

          <h3>Counts</h3>
          <p className="sandbox-counts">
            {Object.entries(result.totals)
              .map(([name, count]) => `${name.replaceAll("_", " ")}: ${count}`)
              .join(" · ")}
          </p>
        </div>
      )}
    </section>
  );
}
