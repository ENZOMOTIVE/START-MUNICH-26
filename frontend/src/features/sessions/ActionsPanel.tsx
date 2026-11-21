import { useCallback, useEffect, useState } from "react";
import {
  editAction,
  listActions,
  resolveAction,
  sendAction,
  type SessionAction,
} from "../../api/client";

// Outbound actions of one session: what was sent, what is waiting, what is uncertain.
// Editing and sending by hand only work while the session is paused (taken over).
export function ActionsPanel({ sessionId, paused }: { sessionId: string; paused: boolean }) {
  const [actions, setActions] = useState<SessionAction[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setActions(await listActions(sessionId));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [sessionId]);

  useEffect(() => {
    void load();
  }, [load, paused]);

  const act = async (work: () => Promise<unknown>) => {
    setError("");
    try {
      await work();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
    await load();
  };

  return (
    <section className="actions" aria-labelledby="actions-heading">
      <h2 id="actions-heading">Outbound actions</h2>
      {!paused && <p className="sandbox-note">Pause the session to edit or send by hand.</p>}
      {error && (
        <p className="sandbox-error" role="alert">
          {error}
        </p>
      )}
      <ul className="sandbox-mails">
        {actions.map((action) => {
          const draft = action.delivery_status === "pending" || action.delivery_status === "failed";
          const text = drafts[action.id] ?? action.body ?? "";
          return (
            <li key={action.id} className="sandbox-mail out">
              <p className="sandbox-mail-head">
                <span className={`pill ${action.delivery_status === "UNKNOWN" ? "exception" : ""}`}>
                  {action.delivery_status}
                </span>{" "}
                {action.purpose ?? action.kind}
                {action.to ? ` · to ${action.to}` : ""}
                {action.edited_by_operator ? " · edited by operator" : ""}
                {action.simulated ? " · simulated" : ""}
              </p>
              {action.body &&
                (draft && paused ? (
                  <textarea
                    aria-label={`Draft for ${action.purpose ?? action.kind}`}
                    rows={8}
                    value={text}
                    onChange={(event) => setDrafts({ ...drafts, [action.id]: event.target.value })}
                  />
                ) : (
                  <pre>{action.body}</pre>
                ))}
              {paused && draft && action.body && (
                <p>
                  <button
                    className="btn"
                    type="button"
                    onClick={() => act(() => editAction(sessionId, action.id, text))}
                  >
                    Save draft
                  </button>{" "}
                  <button
                    className="btn btn-primary"
                    type="button"
                    onClick={() => act(() => sendAction(sessionId, action.id))}
                  >
                    Approve and send
                  </button>
                </p>
              )}
              {paused && action.delivery_status === "UNKNOWN" && (
                <p>
                  It is not known whether this was delivered. It will not be resent automatically.{" "}
                  <button
                    className="btn"
                    type="button"
                    onClick={() => act(() => resolveAction(sessionId, action.id, true))}
                  >
                    I checked: it was delivered
                  </button>{" "}
                  <button
                    className="btn"
                    type="button"
                    onClick={() => act(() => resolveAction(sessionId, action.id, false))}
                  >
                    I checked: it was not delivered
                  </button>
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
