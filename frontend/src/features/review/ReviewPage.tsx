import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getReview, resolveMail, type ReviewQueue } from "../../api/client";

// Everything that waits for a person: blocked sessions, mail no agent handled, tariff files
// that differ from the approved ones, and carrier bids that left something open.
export function ReviewPage() {
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [error, setError] = useState("");
  const [token, setToken] = useState(localStorage.getItem("operator_token") ?? "");

  const refresh = useCallback(async () => {
    try {
      setQueue(await getReview());
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const resolve = async (externalId: string, approve: boolean) => {
    setError("");
    try {
      await resolveMail(externalId, approve);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
    await refresh();
  };

  const saveToken = (value: string) => {
    setToken(value);
    if (value) {
      localStorage.setItem("operator_token", value);
    } else {
      localStorage.removeItem("operator_token");
    }
  };

  return (
    <section aria-labelledby="review-heading">
      <div className="toolbar">
        <Link className="back" to="/">
          ← all sessions
        </Link>
        <label className="hint">
          Operator token{" "}
          <input
            type="password"
            autoComplete="off"
            value={token}
            onChange={(event) => saveToken(event.target.value)}
          />
        </label>
      </div>
      <h2 id="review-heading">Waiting for a person</h2>
      {error && (
        <p className="status error" role="alert">
          {error}
        </p>
      )}
      {queue && queue.sessions.length === 0 && queue.mail.length === 0 && (
        <p className="status empty">Nothing is waiting.</p>
      )}

      {queue && queue.sessions.length > 0 && (
        <article className="panel">
          <h3>
            Sessions ({queue.sessions.length} of {queue.sessions_total}, newest)
          </h3>
          <ul className="sandbox-mails">
            {queue.sessions.map((session) => (
              <li key={session.id} className="sandbox-mail">
                <Link to={`/sessions/${session.id}`}>{session.id.slice(0, 8)}</Link>{" "}
                <span className={`pill ${session.stage}`}>{session.stage}</span>{" "}
                <span className="exception">{session.reason}</span>
                {session.paused ? <span className="pill paused">paused</span> : null}
              </li>
            ))}
          </ul>
        </article>
      )}

      {queue && queue.mail.length > 0 && (
        <article className="panel">
          <h3>
            Mail ({queue.mail.length} of {queue.mail_total}, newest)
          </h3>
          <ul className="sandbox-mails">
            {queue.mail.map((mail) => (
              <li key={mail.external_id} className="sandbox-mail">
                <p className="sandbox-mail-head">
                  <span className="pill exception">{mail.kind ?? "mail"}</span> {mail.sender} ·{" "}
                  {mail.subject}
                </p>
                {mail.unmatched && <p>{mail.unmatched.reason}</p>}
                {mail.tariff && (
                  <p>
                    New tariff file, this carrier's prices are on hold:{" "}
                    {mail.tariff.files.map((file) => `${file.filename} (${file.status})`).join(", ")}
                  </p>
                )}
                {mail.bid && (
                  <p>
                    {mail.bid.carrier} offers {mail.bid.currency ?? "?"} {mail.bid.amount ?? "?"}.
                    Not used because {mail.bid.reasons.join("; ")}.
                  </p>
                )}
                <p>
                  {mail.bid && (
                    <>
                      <button
                        className="btn btn-primary"
                        type="button"
                        onClick={() => void resolve(mail.external_id, true)}
                      >
                        Approve and price with it
                      </button>{" "}
                    </>
                  )}
                  <button
                    className="btn"
                    type="button"
                    onClick={() => void resolve(mail.external_id, false)}
                  >
                    {mail.tariff ? "Tariff approved: lift the hold and reprice" : "Mark as handled"}
                  </button>
                </p>
              </li>
            ))}
          </ul>
        </article>
      )}
    </section>
  );
}
