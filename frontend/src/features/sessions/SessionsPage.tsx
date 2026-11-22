import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { listSessions, runDemo } from "../../api/client";
import { kg, money, humanize, timeAgo } from "../../lib/format";
import { useNow, usePolling } from "../../lib/usePolling";
import type { SessionSummary } from "../../types/session";
import { DeadlineRing, Icon, Lane, StageProgress, StatusChips } from "./bits";
import {
  FILTERS,
  type FilterId,
  isLive,
  laneOf,
  matchesFilter,
  matchesQuery,
  msUntil,
  needsAttention,
  parseApiError,
  type SortId,
  sortSessions,
  stageIndex,
} from "./model";
import "./sessions.css";

const FILTER_IDS = FILTERS.map((f) => f.id) as string[];

export function SessionsPage() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const now = useNow(1000);
  const { data, error, loading, refresh } = usePolling(listSessions, 4000, "sessions");
  const [demoState, setDemoState] = useState<{ running: boolean; error: string | null }>({
    running: false,
    error: null,
  });

  const query = params.get("q") ?? "";
  const filterParam = params.get("f") ?? "all";
  const filter: FilterId = FILTER_IDS.includes(filterParam) ? (filterParam as FilterId) : "all";
  const sort: SortId = params.get("sort") === "newest" ? "newest" : "deadline";

  const update = (key: string, value: string, fallback: string) => {
    const next = new URLSearchParams(params);
    if (!value || value === fallback) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const sessions = data ?? [];
  const searched = useMemo(() => sessions.filter((s) => matchesQuery(s, query)), [sessions, query]);
  const counts = useMemo(() => {
    const out = {} as Record<FilterId, number>;
    for (const f of FILTERS) out[f.id] = searched.filter((s) => matchesFilter(s, f.id, now)).length;
    return out;
  }, [searched, now]);
  const rows = useMemo(
    () =>
      sortSessions(
        searched.filter((s) => matchesFilter(s, filter, now)),
        sort,
        now,
        filter === "all" && sort === "deadline",
      ),
    [searched, filter, sort, now],
  );

  const totals = useMemo(
    () => ({
      live: sessions.filter((s) => isLive(s, now)).length,
      attention: sessions.filter((s) => needsAttention(s, now)).length,
    }),
    [sessions, now],
  );

  const onRunDemo = async () => {
    setDemoState({ running: true, error: null });
    try {
      const result = await runDemo();
      await refresh();
      navigate(`/sessions/${result.id}`);
    } catch (err) {
      const parsed = parseApiError(err);
      setDemoState({
        running: false,
        error: parsed.status === 404 ? "This backend build has no demo runner." : parsed.message,
      });
      return;
    }
    setDemoState({ running: false, error: null });
  };

  return (
    <div className="page ss-page">
      <header className="page-head ss-head">
        <div>
          <h1 className="display">Sessions</h1>
          <p>Every request the agents are working on. Find one, open it, step in.</p>
        </div>
        <dl className="ss-head-stats" aria-label="Session counts">
          <div>
            <dt>Total</dt>
            <dd className="num">{data ? sessions.length : "–"}</dd>
          </div>
          <div>
            <dt>Live now</dt>
            <dd className="num">{data ? totals.live : "–"}</dd>
          </div>
          <div className={totals.attention ? "is-alert" : undefined}>
            <dt>Need a person</dt>
            <dd className="num">{data ? totals.attention : "–"}</dd>
          </div>
        </dl>
      </header>

      <section className="ss-tools" aria-label="Find a session">
        <label className="ss-search">
          <span className="visually-hidden">Search sessions</span>
          <Icon name="search" size={18} />
          <input
            className="input"
            type="search"
            placeholder="Search id, lane (FRA LAX), carrier, thread"
            value={query}
            onChange={(e) => update("q", e.target.value, "")}
          />
        </label>
        <label className="ss-sort">
          <span className="subtle">Sort</span>
          <select
            className="select"
            value={sort}
            onChange={(e) => update("sort", e.target.value, "deadline")}
          >
            <option value="deadline">Deadline soonest</option>
            <option value="newest">Newest</option>
          </select>
        </label>
        <div className="ss-pills" role="group" aria-label="Filter by status">
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              className={`ss-pill${filter === f.id ? " is-on" : ""}${f.id === "attention" && counts.attention ? " is-alert" : ""}`}
              aria-pressed={filter === f.id}
              onClick={() => update("f", f.id, "all")}
            >
              {f.label}
              <span className="ss-pill-count num">{data ? counts[f.id] : "–"}</span>
            </button>
          ))}
        </div>
      </section>

      <div className="ss-status-line subtle" aria-live="polite">
        <span className={`dot${error ? "" : " dot-live"}`} aria-hidden="true" />
        {error && data
          ? "Connection lost, showing the last list. Retrying every 4 s."
          : `Updates every 4 s · ${rows.length} shown`}
      </div>

      {loading && !data ? (
        <ul className="ss-list" aria-busy="true" aria-label="Loading sessions">
          {[0, 1, 2, 3, 4].map((i) => (
            <li key={i} className="ss-row ss-row-skeleton">
              <span className="skeleton" style={{ width: "40%", height: 28 }} />
              <span className="skeleton" style={{ width: "70%" }} />
            </li>
          ))}
        </ul>
      ) : error && !data ? (
        <div className="error-box" role="alert">
          Could not load sessions. {parseApiError(error).message}
          <div>
            <button type="button" className="btn btn-sm ss-mt" onClick={() => void refresh()}>
              Try again
            </button>
          </div>
        </div>
      ) : sessions.length === 0 ? (
        <div className="empty">
          <p>No sessions yet. They appear here as soon as a request email arrives.</p>
          <button
            type="button"
            className="btn btn-accent ss-mt"
            disabled={demoState.running}
            onClick={() => void onRunDemo()}
          >
            {demoState.running ? "Running demo…" : "Run a demo request"}
          </button>
          {demoState.error && <p className="ss-note ss-mt">{demoState.error}</p>}
        </div>
      ) : rows.length === 0 ? (
        <div className="empty">
          <p>Nothing matches{query ? ` “${query}”` : ""} in {FILTERS.find((f) => f.id === filter)?.label}.</p>
          <button type="button" className="btn btn-sm ss-mt" onClick={() => setParams(new URLSearchParams(), { replace: true })}>
            Clear search and filter
          </button>
        </div>
      ) : (
        <ul className="ss-list">
          {rows.map((s) => (
            <SessionRow key={s.id} session={s} now={now} from={location.search} />
          ))}
        </ul>
      )}
    </div>
  );
}

function SessionRow({ session: s, now, from }: { session: SessionSummary; now: number; from: string }) {
  const lane = laneOf(s);
  const attention = needsAttention(s, now);
  const rfq = (s.rfq ?? {}) as Record<string, unknown>;
  const intent = typeof rfq.service_intent === "string" ? rfq.service_intent : null;
  const open = msUntil(s.deadline, now) > 0;
  return (
    <li className={`ss-row${attention ? " is-attention" : ""}`}>
      <Link className="ss-row-link" to={`/sessions/${s.id}`} state={{ from }}>
        <span className="ss-row-lane">
          <Lane origin={lane.origin} destination={lane.destination} />
          <span className="ss-row-meta subtle">
            <span className="num">{kg(s.rfq?.weight_kg ?? s.rfq?.weight)}</span>
            {intent && <span>· {humanize(intent)}</span>}
            <span className="mono">· {s.id.slice(0, 8)}</span>
          </span>
        </span>
        <span className="ss-row-stage">
          <StageProgress stage={s.stage} />
          <span className="ss-row-chips">
            <StatusChips paused={s.paused} exception={s.exception} />
            {s.simulated && <span className="chip">Simulated</span>}
          </span>
        </span>
        <span className="ss-row-quote">
          {s.quote_total ? (
            <>
              <span className="ss-row-total num">{money(s.quote_total, s.quote_currency ?? "EUR")}</span>
              <span className="subtle">{s.carrier ?? "Carrier not given"}</span>
            </>
          ) : (
            <span className="subtle">
              {stageIndex(s.stage) >= 2 ? "Quoted · open for the total" : "No quote yet"}
            </span>
          )}
        </span>
        <span className="ss-row-clock">
          <DeadlineRing deadline={s.deadline} now={now} />
          <span className="subtle ss-row-clock-text">
            {open ? "to respond" : `closed ${timeAgo(s.deadline, now)}`}
          </span>
        </span>
        <span className="ss-row-go" aria-hidden="true">
          <Icon name="back" size={18} />
        </span>
      </Link>
    </li>
  );
}
