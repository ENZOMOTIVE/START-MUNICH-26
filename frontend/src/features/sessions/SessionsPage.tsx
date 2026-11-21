import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listSessions, runDemo } from "../../api/client";
import type { SessionSummary } from "../../types/session";

function formatDeadline(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

export function SessionsPage() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const rows = await listSessions();
      setSessions(rows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load sessions");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const onRunDemo = async () => {
    setRunning(true);
    try {
      await runDemo();
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "demo run failed");
    } finally {
      setRunning(false);
    }
  };

  return (
    <section>
      <div className="toolbar">
        <p className="hint">Live board · polls every 2s</p>
        <button type="button" onClick={() => void onRunDemo()} disabled={running}>
          {running ? "Running demo…" : "Run demo"}
        </button>
      </div>

      {sessions === null && !error ? <p className="status">Loading sessions…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {sessions && sessions.length === 0 && !error ? (
        <p className="status">No sessions yet. Run the demo to seed a FRA→LAX journey.</p>
      ) : null}

      {sessions && sessions.length > 0 ? (
        <ul className="session-list">
          {sessions.map((session) => (
            <li key={session.id}>
              <Link className="session-card" to={`/sessions/${session.id}`}>
                <div>
                  <div className="lane">
                    {session.rfq
                      ? `${session.rfq.origin} → ${session.rfq.destination}`
                      : session.id}
                  </div>
                  <div className="meta-row">
                    <span className={`pill ${session.stage}`}>{session.stage}</span>
                    <span>deadline {formatDeadline(session.deadline)}</span>
                    {session.paused ? <span>paused</span> : null}
                    {session.exception ? (
                      <span className="exception">{session.exception}</span>
                    ) : null}
                  </div>
                </div>
                <span className={`pill ${session.stage}`}>{session.stage}</span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
