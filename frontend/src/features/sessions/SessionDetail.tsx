import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getEvents, getSession, pauseSession, resumeSession } from "../../api/client";
import type { SessionDetail as SessionDetailModel, SessionEvent } from "../../types/session";

function formatWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function weightOf(session: SessionDetailModel): string {
  const rfq = session.request ?? session.rfq;
  return rfq?.weight ?? rfq?.weight_kg ?? "—";
}

export function SessionDetail() {
  const { id } = useParams();
  const [session, setSession] = useState<SessionDetailModel | null>(null);
  const [events, setEvents] = useState<SessionEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    if (!id) {
      return;
    }
    try {
      const [detail, timeline] = await Promise.all([getSession(id), getEvents(id)]);
      setSession(detail);
      setEvents(detail.events && detail.events.length > 0 ? detail.events : timeline);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load session");
    }
  }, [id]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const onPause = async () => {
    if (!id) {
      return;
    }
    setBusy(true);
    try {
      await pauseSession(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "pause failed");
    } finally {
      setBusy(false);
    }
  };

  const onResume = async () => {
    if (!id) {
      return;
    }
    setBusy(true);
    try {
      await resumeSession(id);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "resume failed");
    } finally {
      setBusy(false);
    }
  };

  if (!id) {
    return <p className="status error">Missing session id.</p>;
  }

  const request = session?.request ?? session?.rfq;
  const offer = session?.selected_offer;
  const evidence = session?.evidence ?? offer?.evidence;
  const quote = session?.quote;
  const booking = session?.booking;

  return (
    <section>
      <div className="toolbar">
        <Link className="back" to="/">
          ← all sessions
        </Link>
        <div className="actions">
          <button type="button" className="danger" disabled={busy || !session || session.paused} onClick={() => void onPause()}>
            Pause
          </button>
          <button type="button" disabled={busy || !session || !session.paused} onClick={() => void onResume()}>
            Resume
          </button>
        </div>
      </div>

      {!session && !error ? <p className="status">Loading session…</p> : null}
      {error ? <p className="status error">{error}</p> : null}
      {!session && error ? <p className="status">Session not found or API unreachable.</p> : null}

      {session ? (
        <div className="detail-grid">
          <article className="panel">
            <h2>Request</h2>
            <dl className="kv">
              <dt>id</dt>
              <dd>{session.id}</dd>
              <dt>origin</dt>
              <dd>{request?.origin ?? "—"}</dd>
              <dt>destination</dt>
              <dd>{request?.destination ?? "—"}</dd>
              <dt>weight</dt>
              <dd>{weightOf(session)}</dd>
              <dt>deadline</dt>
              <dd>{formatWhen(session.deadline)}</dd>
              <dt>stage</dt>
              <dd>{session.stage}</dd>
              <dt>paused</dt>
              <dd>{session.paused ? "yes" : "no"}</dd>
              <dt>exception</dt>
              <dd className={session.exception ? "exception" : undefined}>
                {session.exception ?? "none"}
              </dd>
            </dl>
          </article>

          <article className="panel">
            <h2>Selected offer + evidence</h2>
            <dl className="kv">
              <dt>carrier</dt>
              <dd>{offer?.carrier ?? quote?.carrier ?? "—"}</dd>
              <dt>total</dt>
              <dd>{offer?.total ?? quote?.total ?? "—"}</dd>
              <dt>currency</dt>
              <dd>{offer?.currency ?? quote?.currency ?? "—"}</dd>
              <dt>source</dt>
              <dd>
                {offer?.source_ref ?? "—"}
                {offer?.source_version ? ` @ ${offer.source_version}` : ""}
              </dd>
              <dt>simulated</dt>
              <dd>{String(offer?.simulated ?? session.simulated ?? true)}</dd>
            </dl>
            {evidence ? (
              <pre className="event-when">{JSON.stringify(evidence, null, 2)}</pre>
            ) : (
              <p className="hint">No evidence payload yet.</p>
            )}
          </article>

          <article className="panel">
            <h2>Quote / booking</h2>
            <dl className="kv">
              <dt>quote total</dt>
              <dd>{quote ? `${quote.total} ${quote.currency}` : "—"}</dd>
              <dt>quote carrier</dt>
              <dd>{quote?.carrier ?? "—"}</dd>
              <dt>booking status</dt>
              <dd>{booking?.status ?? "—"}</dd>
              <dt>booking ref</dt>
              <dd>{booking?.reference ?? booking?.booking_reference ?? "—"}</dd>
            </dl>
          </article>

          <article className="panel timeline">
            <h2>Timeline</h2>
            {events.length === 0 ? (
              <p className="hint">No events yet.</p>
            ) : (
              <ol>
                {events.map((event) => (
                  <li key={event.id}>
                    <span className="event-type">{event.type}</span>
                    <span className="event-when">
                      {formatWhen(event.occurred_at)}
                      {event.simulated ? " · simulated" : ""}
                    </span>
                  </li>
                ))}
              </ol>
            )}
          </article>
        </div>
      ) : null}
    </section>
  );
}
