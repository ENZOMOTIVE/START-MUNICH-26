import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { getSession, streamSessionEvents } from "../../api/client";
import { humanize, kg } from "../../lib/format";
import { useNow, usePolling } from "../../lib/usePolling";
import type { SessionEvent } from "../../types/session";
import { Icon, Lane, StatusChips } from "./bits";
import { DeadlineClock } from "./DeadlineClock";
import { IntervenePanel } from "./IntervenePanel";
import { dateTime, errorStatus, parseApiError, viewOf } from "./model";
import { BookingCard, QuoteCard } from "./QuoteCard";
import { StageStepper } from "./StageStepper";
import { Timeline } from "./Timeline";
import "./sessions.css";

type Feed = "connecting" | "streaming" | "reconnecting" | "polling";

/**
 * Live events for one session. Uses the SSE stream when the backend has it; if the very first
 * connection fails (this backend build answers 404), it stops trying and the page polls instead.
 */
function useSessionStream(id: string, onEvent: () => void) {
  const [feed, setFeed] = useState<Feed>("connecting");
  const [streamed, setStreamed] = useState<SessionEvent[]>([]);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    setFeed("connecting");
    setStreamed([]);
    let everOpen = false;
    let stopped = false;
    const stop = streamSessionEvents(
      id,
      (event) => {
        setStreamed((prev) => (prev.some((e) => e.id === event.id) ? prev : [...prev, event]));
        onEventRef.current();
      },
      (status) => {
        if (stopped) return;
        if (status === "open") {
          everOpen = true;
          setFeed("streaming");
        } else if (!everOpen) {
          stopped = true;
          stop();
          setFeed("polling");
        } else {
          setFeed("reconnecting");
        }
      },
    );
    return () => {
      stopped = true;
      stop();
    };
  }, [id]);

  return { feed, streamed };
}

export function SessionDetail() {
  const { id = "" } = useParams();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "";
  const backTo = `/sessions${from}`;
  const now = useNow(1000);

  // While streaming, events arrive by SSE and the record is re-read after each one (plus a slow
  // safety poll). Without a stream the record is polled every 3 s.
  const refreshRef = useRef<() => void>(() => {});
  const debounce = useRef(0);
  const onStreamEvent = useCallback(() => {
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(() => refreshRef.current(), 300);
  }, []);
  const { feed, streamed } = useSessionStream(id, onStreamEvent);
  const { data, error, loading, refresh } = usePolling(
    () => getSession(id),
    feed === "streaming" ? 15_000 : 3000,
    id,
  );
  refreshRef.current = () => void refresh();
  useEffect(() => () => window.clearTimeout(debounce.current), []);

  const view = useMemo(() => (data && data.id === id ? viewOf(data) : null), [data, id]);
  const events = useMemo(() => {
    const byId = new Map<string, SessionEvent>();
    for (const e of view?.events ?? []) byId.set(e.id, e);
    for (const e of streamed) if (!byId.has(e.id)) byId.set(e.id, e);
    return [...byId.values()];
  }, [view, streamed]);

  const back = (
    <Link className="ss-back" to={backTo}>
      <Icon name="back" size={16} /> All sessions
    </Link>
  );

  if (!view) {
    if (loading || !error) {
      return (
        <div className="page ss-page" aria-busy="true">
          {back}
          <span className="skeleton" style={{ display: "block", width: "50%", height: 64, margin: "16px 0" }} />
          <div className="ss-detail-grid">
            <div className="grid">
              <span className="skeleton" style={{ height: 180 }} />
              <span className="skeleton" style={{ height: 240 }} />
            </div>
            <span className="skeleton" style={{ height: 320 }} />
          </div>
        </div>
      );
    }
    const notFound = errorStatus(error) === 404;
    return (
      <div className="page ss-page">
        {back}
        <div className={notFound ? "empty ss-mt" : "error-box ss-mt"} role={notFound ? undefined : "alert"}>
          {notFound
            ? "This session does not exist (any more). It may belong to an earlier demo run."
            : `Could not load this session. ${parseApiError(error).message}`}
          {!notFound && (
            <div>
              <button type="button" className="btn btn-sm ss-mt" onClick={() => void refresh()}>
                Try again
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  const req = view.request;
  const origin = req?.origin ?? "—";
  const destination = req?.destination ?? "—";

  return (
    <div className="page ss-page">
      {back}
      <header className="ss-detail-head">
        <div className="ss-detail-title">
          <h1>
            <Lane origin={origin} destination={destination} className="ss-lane-xl" />
          </h1>
          <div className="ss-detail-chips">
            <span className="chip chip-dark">{humanize(view.stage)}</span>
            <StatusChips paused={view.paused} exception={view.exception} />
            {view.simulated && <span className="chip">Simulated</span>}
            <FeedChip feed={feed} stale={Boolean(error)} />
          </div>
          <p className="muted ss-detail-facts">
            <span className="num">{kg(req?.weight_kg)}</span>
            {req?.volume_m3 && <span className="num"> · {Number(req.volume_m3)} m³</span>}
            {req?.service_intent && <span> · wants {req.service_intent}</span>}
            {req?.pickup_required ? <span> · pickup {req.pickup_zone ? `zone ${req.pickup_zone}` : "required"}</span> : null}
            <span> · received {dateTime(req?.received_at)}</span>
          </p>
          <p className="subtle ss-small">
            Session <span className="mono">{view.id}</span>
            {req?.thread_id && (
              <>
                {" "}
                · thread <span className="mono">{req.thread_id}</span>
              </>
            )}
          </p>
        </div>
      </header>

      {error && (
        <p className="ss-note ss-mt" role="status">
          Lost the connection to the server. Showing the last known state and retrying.
        </p>
      )}

      <StageStepper view={view} events={events} />

      <div className="ss-detail-grid">
        <div className="ss-detail-main">
          <DeadlineClock view={view} now={now} />
          <QuoteCard view={view} now={now} />
          <BookingCard view={view} />
          <Timeline events={events} now={now} live={feed === "streaming"} />
        </div>
        <IntervenePanel view={view} onChanged={() => void refresh()} />
      </div>
    </div>
  );
}

function FeedChip({ feed, stale }: { feed: Feed; stale: boolean }) {
  if (stale) return <span className="chip chip-negative">Offline, retrying</span>;
  if (feed === "streaming")
    return (
      <span className="chip chip-positive">
        <span className="dot dot-live" aria-hidden="true" /> Streaming
      </span>
    );
  if (feed === "reconnecting") return <span className="chip chip-warning">Reconnecting stream</span>;
  if (feed === "connecting") return <span className="chip">Connecting…</span>;
  return (
    <span className="chip" title="This backend build has no event stream, so the page re-reads the session every 3 s">
      <span className="dot" aria-hidden="true" /> Polling every 3 s
    </span>
  );
}
