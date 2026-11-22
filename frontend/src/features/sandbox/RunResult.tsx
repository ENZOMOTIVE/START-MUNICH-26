import type { SandboxResult } from "../../api/client";
import { humanize } from "../../lib/format";
import { RunTimeline } from "./RunTimeline";
import { ThreadView } from "./ThreadView";
import { seconds, show } from "./format";

export function RunResult({ result, stale }: { result: SandboxResult; stale: boolean }) {
  const failed = result.checks.filter((check) => !check.passed).length;
  const totals = Object.entries(result.totals);
  const threads = Object.entries(result.threads);

  return (
    <div className={`sb-result ${stale ? "is-stale" : ""}`}>
      <section className={`sb-verdict ${result.passed ? "is-pass" : "is-fail"}`} aria-label="Verdict">
        <div className="sb-verdict-main">
          <span className="sb-verdict-mark" aria-hidden="true">
            {result.passed ? "✓" : "✕"}
          </span>
          <div>
            <p className="sb-verdict-word display">{result.passed ? "Passed" : "Failed"}</p>
            <p className="sb-verdict-sub">
              {result.title || result.scenario} · seed <span className="num">{result.seed}</span>
            </p>
            <p className="sb-verdict-sub">
              <span className="num">{result.checks.length - failed}</span> of{" "}
              <span className="num">{result.checks.length}</span> checks passed
            </p>
          </div>
        </div>
        <dl className="sb-verdict-stats">
          <div>
            <dt>Simulated</dt>
            <dd className="num">{seconds(result.simulated_seconds)}</dd>
          </div>
          <div>
            <dt>Wall clock</dt>
            <dd className="num">{result.wall_seconds.toFixed(2)} s</dd>
          </div>
          <div className="sb-digest">
            <dt>Digest</dt>
            <dd className="mono" title={result.digest}>
              {result.digest.slice(0, 12)}
            </dd>
          </div>
        </dl>
      </section>

      <RunTimeline result={result} />

      <section className="sb-section" aria-labelledby="sb-checks-h">
        <h2 id="sb-checks-h" className="sb-section-title">
          Checks
        </h2>
        <ul className="sb-checks">
          {result.checks.map((check) => (
            <li key={check.check} className={`sb-check ${check.passed ? "" : "is-fail"}`}>
              <span className={`chip ${check.passed ? "chip-positive" : "chip-negative"}`}>
                {check.passed ? "✓ Pass" : "✕ Fail"}
              </span>
              <span className="sb-check-name mono">{check.check}</span>
              <span className="sb-check-vals">
                <span>
                  <span className="subtle">Expected</span> <span className="mono">{show(check.expected)}</span>
                </span>
                <span>
                  <span className="subtle">Actual</span> <span className="mono">{show(check.actual)}</span>
                </span>
              </span>
            </li>
          ))}
        </ul>
      </section>

      {totals.length > 0 && (
        <section className="sb-section" aria-labelledby="sb-totals-h">
          <h2 id="sb-totals-h" className="sb-section-title">
            Totals
          </h2>
          <dl className="sb-totals">
            {totals.map(([name, count]) => (
              <div key={name} className="sb-total">
                <dt>{humanize(name)}</dt>
                <dd className="num">{count}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      {(result.faults.length > 0 || result.errors.length > 0 || result.rejection_reasons.length > 0) && (
        <section className="sb-section sb-issues" aria-label="Faults, errors and rejections">
          {result.faults.length > 0 && (
            <div className="card">
              <h3 className="card-title">Injected faults</h3>
              <ul className="sb-list">
                {result.faults.map((fault, index) => (
                  <li key={index}>
                    <span className="chip chip-warning num">{seconds(fault.at_s)}</span>{" "}
                    {humanize(fault.fault)} <span className="subtle">on</span>{" "}
                    <span className="mono">{fault.where}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.errors.length > 0 && (
            <div className="card">
              <h3 className="card-title">Errors</h3>
              <ul className="sb-list">
                {result.errors.map((error, index) => (
                  <li key={index}>
                    {typeof error.at_s === "number" && (
                      <span className="chip chip-negative num">{seconds(error.at_s)}</span>
                    )}{" "}
                    <span className="mono">
                      {Object.entries(error)
                        .filter(([key]) => key !== "at_s")
                        .map(([key, value]) => `${key}: ${show(value)}`)
                        .join(" · ")}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.rejection_reasons.length > 0 && (
            <div className="card">
              <h3 className="card-title">Rejection reasons</h3>
              <ul className="sb-list">
                {result.rejection_reasons.map((reason, index) => (
                  <li key={index}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {threads.length > 0 && (
        <section className="sb-section" aria-labelledby="sb-threads-h">
          <h2 id="sb-threads-h" className="sb-section-title">
            Mail threads
          </h2>
          <div className="sb-threads">
            {threads.map(([key, mails]) => (
              <ThreadView
                key={key}
                name={key}
                mails={mails}
                request={result.requests[key] ?? {}}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
