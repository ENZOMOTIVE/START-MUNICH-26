import type { SandboxMail } from "../../api/client";
import { humanize } from "../../lib/format";
import { seconds, show } from "./format";

const FACTS = ["stage", "blocked_on", "extracted", "dangerous_goods", "quote", "booking", "customer"];

/** One request's conversation: inbound left in tint, outbound right in mint. */
export function ThreadView({
  name,
  mails,
  request,
}: {
  name: string;
  mails: SandboxMail[];
  request: Record<string, unknown>;
}) {
  const events = Array.isArray(request.events) ? (request.events as unknown[]).map(String) : [];
  const stage = typeof request.stage === "string" ? request.stage : null;

  return (
    <article className="sb-thread card" aria-label={`Request ${name}`}>
      <header className="sb-thread-head">
        <h3 className="sb-thread-title">Request {name}</h3>
        {stage && <span className="chip chip-dark">{humanize(stage)}</span>}
        <span className="subtle num">{mails.length} mails</span>
      </header>

      <ol className="sb-convo">
        {mails.map((mail, index) => (
          <li key={index} className={`sb-bubble-row is-${mail.direction}`}>
            <div className="sb-bubble">
              <p className="sb-bubble-meta">
                <span className="num">{seconds(mail.at_s)}</span> ·{" "}
                {mail.direction === "in" ? "Received" : `Sent${mail.purpose ? ` · ${humanize(mail.purpose)}` : ""}`}
              </p>
              <p className="sb-bubble-subject">{mail.subject}</p>
              <p className="sb-bubble-body">{mail.body}</p>
              {mail.attachments && mail.attachments.length > 0 && (
                <ul className="sb-attachments" aria-label="Attachments">
                  {mail.attachments.map((file, i) => (
                    <li key={i} className="chip">
                      <span className="sb-clip" aria-hidden="true" /> {file}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>

      <details className="sb-facts">
        <summary>Request state</summary>
        <dl className="kv sb-kv">
          {FACTS.map((field) => (
            <div key={field} className="sb-kv-row">
              <dt>{humanize(field)}</dt>
              <dd className="mono">{show(request[field])}</dd>
            </div>
          ))}
          <div className="sb-kv-row">
            <dt>State transitions</dt>
            <dd>{events.length ? events.join(" → ") : "none"}</dd>
          </div>
        </dl>
      </details>
    </article>
  );
}
