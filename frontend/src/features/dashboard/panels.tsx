import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { RunStatus, UsageRow } from "../../api/client";
import { BarList, Donut, Gauge, Histogram, Meter, Sparkline, StackedBars, StageFunnel } from "../../components/charts";
import { COST_ZERO_USD, RESPONSE_FULL_MS, RESPONSE_ZERO_MS, duration, humanize, usd } from "../../lib/format";
import type { SessionSummary } from "../../types/session";
import {
  activity,
  countBy,
  isLive,
  median,
  needsPerson,
  pipeline,
  responsePoints,
  stageIndex,
  usageTotals,
  type LooseDetail,
  type ResponseSample,
} from "./metrics";
import type { FeedEvent } from "./useLiveFeed";
import { OUR_TEAM, ROWS, SNAPSHOT, perAccepted, rate, standing } from "./leaderboard";
import type { Optional } from "./useDashboardData";

/* ---------- small pieces ---------- */

export function Panel({
  title,
  sub,
  aside,
  className = "",
  children,
  id,
}: {
  title: string;
  sub?: ReactNode;
  aside?: ReactNode;
  className?: string;
  children: ReactNode;
  id: string;
}) {
  return (
    <section className={`card db-panel ${className}`} aria-labelledby={id}>
      <div className="card-head">
        <div>
          <h2 className="card-title" id={id}>
            {title}
          </h2>
          {sub ? <p className="card-sub">{sub}</p> : null}
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <p className="db-note">
      <span className="db-note-icon" aria-hidden="true">
        i
      </span>
      <span>{children}</span>
    </p>
  );
}

export function SkeletonBlock({ rows = 3 }: { rows?: number }) {
  return (
    <div className="db-skel" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ height: 18, width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function StageChip({ s }: { s: SessionSummary }) {
  const cls =
    s.stage === "booked"
      ? "chip-positive"
      : s.stage === "quoted" || s.stage === "accepted"
        ? "chip-info"
        : "";
  return <span className={`chip ${cls}`}>{humanize(s.stage)}</span>;
}

const minutes = (ms: number) => `${Math.round((ms / 60_000) * 10) / 10}m`;

/* ---------- pipeline ---------- */

export function PipelinePanel({ list }: { list: SessionSummary[] }) {
  const p = pipeline(list);
  const colors = ["var(--tint-active)", "var(--series-3)", "var(--series-2)", "var(--series-4)", "var(--series-1)"];
  return (
    <Panel
      id="db-pipeline"
      title="Pipeline"
      sub="Sessions that reached each stage. “Now” counts those sitting in it."
      className="db-span-7"
    >
      <StageFunnel
        label="Pipeline from intake to booked"
        stages={p.reached.map((r, i) => ({
          key: r.stage,
          label: humanize(r.stage),
          value: r.reached,
          color: colors[i],
          caption: i === 0 ? `${r.now} now` : `${r.now} now · ${pct(r.reached, list.length)}`,
        }))}
      />
      <div className="db-lost" aria-label="Lost or held">
        <span className="db-lost-title">Lost / held</span>
        <span className="chip chip-negative">
          <span aria-hidden="true">✕</span> Expired <strong className="num">{p.expired}</strong>
        </span>
        <span className="chip chip-warning">
          <span aria-hidden="true">❚❚</span> Paused <strong className="num">{p.paused}</strong>
        </span>
        {p.otherExceptions > 0 ? (
          <span className="chip">
            Other exceptions <strong className="num">{p.otherExceptions}</strong>
          </span>
        ) : null}
      </div>
    </Panel>
  );
}

function pct(n: number, d: number) {
  return d > 0 ? `${Math.round((n / d) * 100)}%` : "—";
}

/* ---------- at risk ---------- */

function Countdown({ deadline, now }: { deadline: string; now: number }) {
  const left = Date.parse(deadline) - now;
  if (!Number.isFinite(left)) return <span className="chip">—</span>;
  if (left <= 0) return <span className="chip">Closed</span>;
  const cls = left < RESPONSE_FULL_MS ? "chip-negative" : left < 4 * 60_000 ? "chip-warning" : "chip-positive";
  return (
    <span className={`chip num ${cls}`}>
      <span className="dot dot-live" aria-hidden="true" />
      {duration(left)} left
    </span>
  );
}

function RiskRow({ s, now, reason }: { s: SessionSummary; now: number; reason?: string }) {
  const lane = `${s.origin ?? s.rfq?.origin ?? "?"} → ${s.destination ?? s.rfq?.destination ?? "?"}`;
  return (
    <li>
      <Link className="db-risk-row" to={`/sessions/${s.id}`}>
        <span className="db-risk-main">
          <strong>{lane}</strong>
          <span className="subtle mono">{s.id.slice(0, 8)}</span>
        </span>
        <span className="db-risk-chips">
          {reason ? <span className="chip chip-negative">{reason}</span> : <StageChip s={s} />}
          <Countdown deadline={s.deadline} now={now} />
        </span>
      </Link>
    </li>
  );
}

export function AtRiskPanel({ list, now }: { list: SessionSummary[]; now: number }) {
  const open = list
    .filter((s) => stageIndex(s.stage) < 2 && !needsPerson(s) && isLive(s, now))
    .sort((a, b) => Date.parse(a.deadline) - Date.parse(b.deadline))
    .slice(0, 6);
  const flagged = list
    .filter(needsPerson)
    .sort((a, b) => Date.parse(b.deadline) - Date.parse(a.deadline));
  return (
    <Panel
      id="db-risk"
      title="At risk"
      sub="Open RFQs closest to expiry, then sessions waiting for a person."
      className="db-span-5"
      aside={
        <Link className="btn btn-sm" to="/review">
          Review queue
        </Link>
      }
    >
      <h3 className="db-sub-title">Against the clock</h3>
      {open.length ? (
        <ul className="db-risk" aria-live="polite">
          {open.map((s) => (
            <RiskRow key={s.id} s={s} now={now} />
          ))}
        </ul>
      ) : (
        <p className="db-quiet">Nothing unquoted is open right now.</p>
      )}
      <h3 className="db-sub-title">
        Needs a person <span className="subtle num">({flagged.length})</span>
      </h3>
      {flagged.length ? (
        <ul className="db-risk">
          {flagged.slice(0, 6).map((s) => (
            <RiskRow key={s.id} s={s} now={now} reason={s.paused && !s.exception ? "Paused" : humanize(s.exception)} />
          ))}
        </ul>
      ) : (
        <p className="db-quiet">No exceptions. The desk runs on its own.</p>
      )}
      {flagged.length > 6 ? (
        <Link className="db-more" to="/sessions">
          See all {flagged.length} in Sessions →
        </Link>
      ) : null}
    </Panel>
  );
}

/* ---------- response time ---------- */

export function ResponsePanel({
  samples,
  loaded,
  settled,
  failed,
}: {
  samples: ResponseSample[];
  loaded: number;
  settled: boolean;
  failed: number;
}) {
  const ms = samples.map((s) => s.ms);
  const med = median(ms);
  const inside = ms.filter((v) => v <= RESPONSE_FULL_MS).length;
  const avgPts = ms.length ? ms.reduce((sum, v) => sum + responsePoints(v), 0) / ms.length : null;
  return (
    <Panel
      id="db-response"
      title="Response time vs the scoring window"
      sub={`Quote decided − RFQ received, latest ${loaded} sessions with details loaded. Full points ≤2 min, zero at 6 min.`}
      className="db-span-8"
    >
      {!settled && samples.length === 0 ? (
        <SkeletonBlock rows={5} />
      ) : samples.length === 0 ? (
        <div className="empty">
          No loaded session has both a received and a quote-decided timestamp yet.
          {failed > 0 ? ` (${failed} detail requests failed.)` : ""}
        </div>
      ) : (
        <>
          <div className="db-stats">
            <Stat label="Median" value={med !== null ? duration(med) : "—"} />
            <Stat label="Inside 2 min" value={pct(inside, ms.length)} hint={`${inside} of ${ms.length}`} />
            <Stat
              label="Avg time points"
              value={avgPts !== null ? `${avgPts.toFixed(1)}/15` : "—"}
              hint="derived"
            />
          </div>
          <Histogram
            label="Quote response times"
            values={ms}
            max={8 * 60_000}
            binSize={30_000}
            formatTick={minutes}
            height={220}
            bands={[
              { from: 0, to: RESPONSE_FULL_MS, color: "var(--lime)", label: "Full ≤2m" },
              { from: RESPONSE_FULL_MS, to: RESPONSE_ZERO_MS, color: "var(--warning)", label: "Sliding 2–6m" },
              { from: RESPONSE_ZERO_MS, to: Infinity, color: "var(--negative)", label: "Zero ≥6m" },
            ]}
            markers={med !== null ? [{ value: med, label: `Median ${duration(med)}` }] : []}
          />
          <div className="db-trend">
            <span className="subtle">Trend, oldest → newest</span>
            <Sparkline
              label="Response time per quote, oldest to newest"
              values={ms}
              pointLabels={samples.map((s) => new Date(s.receivedAt).toLocaleTimeString("en-GB"))}
              reference={{ value: RESPONSE_FULL_MS, label: "2 min full-points line" }}
              formatValue={duration}
              height={48}
            />
          </div>
        </>
      )}
    </Panel>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="db-stat">
      <span className="db-stat-label">{label}</span>
      <strong className="num">{value}</strong>
      {hint ? <span className="subtle">{hint}</span> : null}
    </div>
  );
}

/* ---------- LLM cost ---------- */

export function CostPanel({
  usage,
  error,
  quoted,
}: {
  usage: Optional<UsageRow[]> | null;
  error: string | null;
  quoted: number;
}) {
  let body: ReactNode;
  if (!usage && error) body = <Note>Usage could not be loaded: {error}</Note>;
  else if (!usage) body = <SkeletonBlock rows={4} />;
  else if (!usage.available)
    body = <Note>LLM usage is not exposed by this backend build yet. It lights up once /api/usage responds.</Note>;
  else if (usage.data.length === 0) body = <Note>No model gateway calls recorded yet.</Note>;
  else {
    const t = usageTotals(usage.data);
    const per = quoted > 0 ? t.spend / quoted : null;
    body = (
      <>
        {per !== null ? (
          <Gauge
            label="LLM cost per quote against the cap"
            value={per}
            max={COST_ZERO_USD}
            valueText={usd(per)}
            readout={usd(per, per < 0.01 ? 4 : 3)}
            caption={`per quote · ≈${Math.max(0, 40 * (1 - per / COST_ZERO_USD)).toFixed(1)}/40 pts (derived)`}
            maxLabel={usd(COST_ZERO_USD, 2)}
            bands={[
              { to: 0.03, color: "var(--lime)" },
              { to: 0.07, color: "var(--warning)" },
              { to: COST_ZERO_USD, color: "var(--negative)" },
            ]}
          />
        ) : (
          <Note>No quotes yet, so there is no per-quote cost.</Note>
        )}
        <p className="db-cost-line subtle">
          {usd(t.spend)} over {t.calls} calls ÷ {quoted} quoted
          {t.estimatedOnly ? " · includes estimated costs" : ""}
        </p>
        <BarList
          label="Spend by model"
          items={t.models.slice(0, 5).map((m) => ({ label: m.label, value: m.value }))}
          formatValue={(v) => usd(v)}
        />
      </>
    );
  }
  return (
    <Panel id="db-cost" title="LLM cost per quote" sub={`Gateway spend vs the ${usd(COST_ZERO_USD, 2)} zero-points cap`} className="db-span-4">
      {body}
    </Panel>
  );
}

/* ---------- lanes, carriers, intent ---------- */

export function LanesPanel({ list }: { list: SessionSummary[] }) {
  const lanes = countBy(
    list,
    (s) => {
      const o = s.origin ?? s.rfq?.origin;
      const d = s.destination ?? s.rfq?.destination;
      return o && d ? `${o} → ${d}` : null;
    },
    6,
  );
  return (
    <Panel id="db-lanes" title="Top lanes" sub={`By RFQ count, all ${list.length} sessions`} className="db-span-4">
      {lanes.length ? (
        <BarList
          label="Top lanes by RFQ count"
          items={lanes.map((l) => ({
            label: l.label,
            value: l.value,
            hint: pct(l.value, list.length),
            color: l.label === "Other" ? "var(--tint-active)" : "var(--series-1)",
          }))}
        />
      ) : (
        <div className="empty">No lanes yet.</div>
      )}
    </Panel>
  );
}

export function MixPanel({ details, settled }: { details: LooseDetail[]; settled: boolean }) {
  const carriers = countBy(details, (d) => d.selected_offer?.carrier, 5);
  const intents = countBy(details, (d) => d.request?.service_intent ?? null, 4);
  const withOffer = carriers.reduce((s, c) => s + c.value, 0);
  const intentTotal = intents.reduce((s, c) => s + c.value, 0);
  return (
    <Panel
      id="db-mix"
      title="Carrier mix"
      sub={`Selected offers in the latest ${details.length} sessions`}
      className="db-span-4"
    >
      {!settled && details.length === 0 ? (
        <SkeletonBlock rows={4} />
      ) : withOffer ? (
        <Donut label="Carrier mix" data={carriers} centerLabel="offers" />
      ) : (
        <div className="empty">No selected offers in the loaded sessions.</div>
      )}
      <h3 className="db-sub-title">Service intent</h3>
      {intentTotal ? (
        <div className="db-split" role="img" aria-label={intents.map((i) => `${i.label} ${i.value}`).join(", ")}>
          <div className="db-split-bar" aria-hidden="true">
            {intents.map((i, idx) => (
              <span
                key={i.label}
                style={{ flexGrow: i.value, background: idx === 0 ? "var(--series-1)" : "var(--series-2)" }}
              />
            ))}
          </div>
          <div className="db-split-legend" aria-hidden="true">
            {intents.map((i, idx) => (
              <span key={i.label}>
                <span
                  className="ch-swatch"
                  style={{ background: idx === 0 ? "var(--series-1)" : "var(--series-2)" }}
                />
                {humanize(i.label)} <strong className="num">{i.value}</strong>
                <span className="subtle num">{pct(i.value, intentTotal)}</span>
              </span>
            ))}
          </div>
        </div>
      ) : (
        <p className="db-quiet">No service intent in the loaded details.</p>
      )}
    </Panel>
  );
}

/* ---------- run limits ---------- */

export function RunPanel({ run, error }: { run: Optional<RunStatus> | null; error: string | null }) {
  let body: ReactNode;
  if (!run && error) body = <Note>Run status could not be loaded: {error}</Note>;
  else if (!run) body = <SkeletonBlock rows={3} />;
  else if (!run.available)
    body = <Note>Run limits are not exposed by this backend build yet. They appear once /api/run responds.</Note>;
  else {
    const r = run.data;
    const keys = Object.keys(r.limits);
    body = (
      <>
        <div className="db-run-head">
          <span className={`chip ${r.enabled ? "chip-positive" : ""}`}>
            {r.enabled ? <span className="dot dot-live" aria-hidden="true" /> : null}
            {r.enabled ? "Run enabled" : "Run off"}
          </span>
          <span className="subtle">
            {r.minutes} min window · {r.admitted_threads.length} admitted threads
          </span>
        </div>
        {keys.length ? (
          <div className="db-meters">
            {keys.map((k) => (
              <Meter key={k} label={humanize(k)} value={r.used[k] ?? 0} max={r.limits[k] ?? 0} />
            ))}
          </div>
        ) : (
          <Note>No limits configured.</Note>
        )}
      </>
    );
  }
  return (
    <Panel id="db-run" title="Run limits" sub="Used vs limits of the bounded live run" className="db-span-4">
      {body}
    </Panel>
  );
}


/* ---------- activity over time ---------- */

export function ActivityPanel({ list }: { list: SessionSummary[] }) {
  const { buckets, stepMinutes } = activity(list);
  const showDay = buckets.length > 0 && (buckets[buckets.length - 1]!.start - buckets[0]!.start) / 3_600_000 > 20;
  const fmt = (start: number) => {
    const d = new Date(start);
    const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    return showDay ? `${d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })} ${t}` : t;
  };
  return (
    <Panel
      id="db-activity"
      title="RFQs over time"
      sub={
        stepMinutes
          ? `Received per ${stepMinutes < 60 ? `${stepMinutes} min` : `${stepMinutes / 60} h`}, split by where each one stands now. All ${list.length} sessions.`
          : "Received per interval, split by where each one stands now."
      }
      className="db-span-8"
    >
      {buckets.length ? (
        <StackedBars
          label="RFQs received over time, by outcome"
          height={260}
          buckets={buckets.map((b) => ({ label: fmt(b.start), values: b.values }))}
          series={[
            { key: "booked", label: "Booked", color: "var(--series-1)" },
            { key: "quoted", label: "Quoted or accepted", color: "var(--series-2)" },
            { key: "progress", label: "In progress", color: "var(--series-3)" },
            { key: "attention", label: "Needs attention", color: "var(--negative)" },
          ]}
        />
      ) : (
        <div className="empty">No RFQs yet.</div>
      )}
    </Panel>
  );
}

/* ---------- board standings (snapshot) ---------- */

export function StandingsPanel() {
  const me = standing();
  if (!me) return null;
  const byPoints = [...ROWS].filter((r) => r.points > 0).sort((a, b) => b.points - a.points);
  const byQuality = [...ROWS].filter((r) => r.accepted > 0).sort((a, b) => perAccepted(b) - perAccepted(a));
  const colour = (team: string) => (team === OUR_TEAM ? "var(--series-2)" : "var(--tint-active)");
  const n = (v: number, d = 0) => v.toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d });
  return (
    <Panel
      id="db-standings"
      title="Where we stand on the board"
      sub={`Snapshot of the board at ${SNAPSHOT.updatedAt}, ${SNAPSHOT.roundsSent} rounds sent, ${SNAPSHOT.teams} teams. Copied by hand, so it does not refresh.`}
      className="db-span-12"
    >
      <div className="db-stats">
        <Stat label="Rank by points" value={`${me.rank} of ${me.teams}`} hint={`${n(me.row.points, 1)} points`} />
        <Stat
          label="Points per accepted email"
          value={n(me.perAccepted, 1)}
          hint={`rank ${me.perAcceptedRank} of ${byQuality.length}`}
        />
        <Stat
          label="Accepted of answered"
          value={`${Math.round(rate(me.row) * 100)}%`}
          hint={`${me.row.accepted} of ${me.row.answered}`}
        />
        <Stat
          label="Rounds without an answer"
          value={String(me.roundsMissed)}
          hint={`of ${SNAPSHOT.roundsSent} sent`}
        />
        <Stat label="Bookings" value={String(me.row.bookings)} />
      </div>
      <div className="db-standings">
        <div>
          <h3 className="db-sub-title">Total points</h3>
          <BarList
            label="Total points by team"
            items={byPoints.map((r) => ({
              label: r.team,
              value: r.points,
              hint: r.team === OUR_TEAM ? "us" : undefined,
              color: colour(r.team),
            }))}
            formatValue={(v) => n(v)}
          />
        </div>
        <div>
          <h3 className="db-sub-title">Points per accepted email</h3>
          <BarList
            label="Points per accepted email by team"
            items={byQuality.map((r) => ({
              label: r.team,
              value: perAccepted(r),
              hint: r.team === OUR_TEAM ? "us" : undefined,
              color: colour(r.team),
            }))}
            formatValue={(v) => n(v, 1)}
          />
        </div>
      </div>
    </Panel>
  );
}

/* ---------- live activity feed ---------- */

export function LiveFeedPanel({ events, total, now }: { events: FeedEvent[]; total: number; now: number }) {
  const ago = (at: number) => {
    const s = Math.max(0, Math.round((now - at) / 1000));
    return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ago`;
  };
  return (
    <Panel
      id="db-feed"
      title="Live activity"
      sub="What changed since you opened this page, checked every 2 seconds."
      className="db-span-4"
      aside={
        <span className="chip chip-dark">
          <span className="dot dot-live" aria-hidden="true" />
          <strong className="num">{total}</strong> changes
        </span>
      }
    >
      {events.length ? (
        <ul className="db-feed" aria-live="polite">
          {events.map((e) => (
            <li key={e.key} className={`db-feed-item db-feed-${e.kind}`}>
              <span className="db-feed-dot" aria-hidden="true" />
              <span className="db-feed-main">
                <strong>{e.lane}</strong>
                <span>{e.text}</span>
              </span>
              <span className="subtle num">{ago(e.at)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="empty">Waiting for the next RFQ. New requests and stage changes appear here as they happen.</div>
      )}
    </Panel>
  );
}
