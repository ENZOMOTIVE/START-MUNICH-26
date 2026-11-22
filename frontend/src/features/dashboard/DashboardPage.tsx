import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { clock, money } from "../../lib/format";
import { useNow } from "../../lib/usePolling";
import "./dashboard.css";
import { isLive, kpis, needsPerson, responseTimes } from "./metrics";
import {
  AtRiskPanel,
  CostPanel,
  LanesPanel,
  MixPanel,
  PipelinePanel,
  ResponsePanel,
  RunPanel,
} from "./panels";
import { DETAIL_WINDOW, useDashboardData } from "./useDashboardData";

/** Overview: what's live, what's at risk, how we score. Real API data only. */
export function DashboardPage() {
  const { sessions, meta, usage, run, details } = useDashboardData();
  const now = useNow(1000);
  const list = sessions.data;
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  useEffect(() => {
    if (list) setUpdatedAt(Date.now());
  }, [list]);

  const k = useMemo(() => (list ? kpis(list, details.details) : null), [list, details.details]);
  const samples = useMemo(() => responseTimes(details.details), [details.details]);
  const live = list ? list.filter((s) => isLive(s, now)).length : null;
  const attention = list ? list.filter(needsPerson).length : null;

  return (
    <div className="page db">
      <Hero
        mode={meta.data?.app_mode ?? null}
        live={live}
        attention={attention}
        updatedAt={updatedAt}
        stale={Boolean(sessions.error && list)}
      />

      {sessions.error && !list ? (
        <div className="error-box db-error" role="alert">
          Sessions could not be loaded ({sessions.error}).{" "}
          <button type="button" className="btn btn-sm" onClick={() => void sessions.refresh()}>
            Try again
          </button>
        </div>
      ) : null}

      <section className="db-kpis" aria-label="Key figures">
        <Kpi label="RFQs received" value={k?.received} />
        <Kpi label="Quoted" value={k?.quoted} hint="reached quoted or beyond" />
        <Kpi label="Booked" value={k?.booked} />
        <Kpi
          label="Win rate"
          value={k ? (k.winRate !== null ? `${Math.round(k.winRate * 100)}%` : "—") : undefined}
          hint="booked ÷ quoted or beyond"
          accent
        />
        <Kpi
          label="Needs attention"
          value={k?.attention}
          hint="exception or paused"
          tone={k && k.attention > 0 ? "warn" : undefined}
          to="/review"
        />
        <Kpi
          label="Total quoted value"
          value={k ? (k.eurCount ? money(k.eur, "EUR") : "—") : undefined}
          hint={
            k
              ? k.eurCount
                ? `EUR, sum of ${k.eurCount} quote totals (latest ${DETAIL_WINDOW} loaded)`
                : "no EUR quote totals loaded yet"
              : undefined
          }
          wide
        />
      </section>

      {list ? (
        <div className="db-grid">
          <PipelinePanel list={list} />
          <AtRiskPanel list={list} now={now} />
          <ResponsePanel
            samples={samples}
            loaded={details.details.length}
            settled={details.settled}
            failed={details.failed}
          />
          <CostPanel usage={usage.data} error={usage.error} quoted={k?.quoted ?? 0} />
          <LanesPanel list={list} />
          <MixPanel details={details.details} settled={details.settled} />
          <RunPanel run={run.data} error={run.error} />
        </div>
      ) : sessions.loading ? (
        <div className="db-grid" aria-busy="true" aria-label="Loading overview">
          {[7, 5, 8, 4].map((span, i) => (
            <div key={i} className={`card db-span-${span}`}>
              <div className="skeleton" style={{ height: 20, width: "40%", marginBottom: 20 }} />
              <div className="skeleton" style={{ height: 160 }} />
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Hero({
  mode,
  live,
  attention,
  updatedAt,
  stale,
}: {
  mode: string | null;
  live: number | null;
  attention: number | null;
  updatedAt: number | null;
  stale: boolean;
}) {
  return (
    <section className="db-hero" aria-labelledby="db-hero-title">
      <div className="db-hero-copy">
        <h1 id="db-hero-title" className="display db-hero-title">
          Every RFQ,
          <br />
          answered.
        </h1>
        <p className="db-hero-status" aria-live="polite">
          <span className="chip chip-dark">
            <span className="dot dot-live" aria-hidden="true" />
            {mode ? `${mode} mode` : "mode …"}
          </span>
          <span>
            <strong className="num">{live ?? "–"}</strong> live right now
          </span>
          <span aria-hidden="true" className="subtle">
            ·
          </span>
          <span>
            <strong className="num">{attention ?? "–"}</strong> need a person
          </span>
          {updatedAt ? (
            <span className="subtle db-hero-updated">
              {stale ? "Connection lost, showing data from " : "Updated "}
              {clock(new Date(updatedAt).toISOString())}
            </span>
          ) : null}
        </p>
      </div>
      <div className="db-hero-actions">
        <Link className="btn btn-accent db-cta" to="/sessions">
          Open sessions
          <span aria-hidden="true">→</span>
        </Link>
        <Link className="btn db-cta" to="/review">
          Review queue
        </Link>
      </div>
    </section>
  );
}

function Kpi({
  label,
  value,
  hint,
  accent,
  tone,
  wide,
  to,
}: {
  label: string;
  value: string | number | undefined;
  hint?: string;
  accent?: boolean;
  tone?: "warn";
  wide?: boolean;
  to?: string;
}) {
  const cls = `db-kpi ${accent ? "db-kpi-accent" : ""} ${tone === "warn" ? "db-kpi-warn" : ""} ${wide ? "db-kpi-wide" : ""}`;
  const inner = (
    <>
      <span className="db-kpi-label">{label}</span>
      {value === undefined ? (
        <span className="skeleton db-kpi-skel" aria-hidden="true" />
      ) : (
        <strong className="db-kpi-value num">{value}</strong>
      )}
      {hint ? <span className="db-kpi-hint">{hint}</span> : null}
    </>
  );
  return to ? (
    <Link className={`${cls} db-kpi-link`} to={to}>
      {inner}
    </Link>
  ) : (
    <div className={cls}>{inner}</div>
  );
}
