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

function eventSummary(event: SessionEvent): string {
  const payload = event.payload ?? {};
  const bits = [
    typeof payload.carrier === "string" ? payload.carrier : null,
    typeof payload.total === "string"
      ? `${payload.total}${typeof payload.currency === "string" ? ` ${payload.currency}` : ""}`
      : null,
    typeof payload.booking_reference === "string" ? payload.booking_reference : null,
  ].filter((bit): bit is string => Boolean(bit));
  return bits.join(" · ");
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
    return (
      <p className="status error" role="alert">
        Missing session id.
      </p>
    );
  }

  const request = session?.request ?? session?.rfq;
  const offer = session?.selected_offer;
  const evidence = session?.evidence ?? offer?.evidence;
  const quote = session?.quote;
  const booking = session?.booking;

  return (
    <section aria-labelledby="session-heading">
      <div className="toolbar">
        <Link className="back" to="/">
          ← all sessions
        </Link>
        <div className="actions">
          <button
            type="button"
            className="danger"
            disabled={busy || !session || session.paused}
            aria-pressed={session?.paused === true}
            onClick={() => void onPause()}
          >
            Pause
          </button>
          <button type="button" disabled={busy || !session || !session.paused} onClick={() => void onResume()}>
            Resume
          </button>
        </div>
      </div>

      {!session && !error ? (
        <div className="skeleton" aria-busy="true">
          <div className="skeleton-row" />
          <div className="skeleton-row" />
        </div>
      ) : null}
      {error ? (
        <p className="status error" role="alert">
          {error}
        </p>
      ) : null}

      {session ? (
        <div className="detail-grid">
          <article className="panel">
            <h2 id="session-heading">Request</h2>
            <p className="hero-lane">
              {request?.origin ?? "—"} → {request?.destination ?? "—"}
            </p>
            <dl className="kv">
              <dt>weight</dt>
              <dd>{weightOf(session)}</dd>
              <dt>deadline</dt>
              <dd>{formatWhen(session.deadline)}</dd>
              <dt>stage</dt>
              <dd>
                <span className={`pill ${session.stage}`}>{session.stage}</span>
              </dd>
              <dt>paused</dt>
              <dd>{session.paused ? "yes" : "no"}</dd>
              <dt>exception</dt>
              <dd className={session.exception ? "exception" : undefined}>{session.exception ?? "none"}</dd>
            </dl>
          </article>

          <article className="panel">
            <h2>Selected offer</h2>
            <dl className="kv">
              <dt>carrier</dt>
              <dd>{offer?.carrier ?? quote?.carrier ?? "—"}</dd>
              <dt>total</dt>
              <dd className="quote">{offer?.total ?? quote?.total ?? "—"}</dd>
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
              <details className="evidence">
                <summary>Evidence payload</summary>
                <pre>{JSON.stringify(evidence, null, 2)}</pre>
              </details>
            ) : (
              <p className="hint">No evidence payload yet.</p>
            )}
          </article>

          <article className="panel">
            <h2>Quote / booking</h2>
            <dl className="kv">
              <dt>quote total</dt>
              <dd className="quote">{quote ? `${quote.total} ${quote.currency}` : "—"}</dd>
              <dt>quote carrier</dt>
              <dd>{quote?.carrier ?? offer?.carrier ?? "—"}</dd>
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
              <ol aria-label="Session events">
                {events.map((event) => (
                  <li key={event.id}>
                    <span className="event-type">{event.type}</span>
                    {eventSummary(event) ? <span className="event-when">{eventSummary(event)}</span> : null}
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
