/** Pure derivations for the overview. Every number here comes from API data. */
import type { UsageRow } from "../../api/client";
import { RESPONSE_FULL_MS, RESPONSE_ZERO_MS } from "../../lib/format";
import type { SessionDetail, SessionSummary } from "../../types/session";

/**
 * The detail endpoint returns more than the shared hand-written type declares
 * (received_at, service_intent, decided_at). Read those fields defensively here.
 */
export type LooseDetail = Omit<SessionDetail, "request" | "quote"> & {
  request?: {
    origin?: string;
    destination?: string;
    service_intent?: string | null;
    received_at?: string | null;
    expires_at?: string | null;
  } | null;
  quote?: { total?: string | null; currency?: string | null; decided_at?: string | null } | null;
};

export const STAGES = ["intake", "pricing", "quoted", "accepted", "booked"] as const;

export function stageIndex(stage: string): number {
  return (STAGES as readonly string[]).indexOf(stage);
}

export const isQuotedOrBeyond = (s: SessionSummary) => stageIndex(s.stage) >= 2;
export const needsPerson = (s: SessionSummary) => s.exception !== null || s.paused;
export const isLive = (s: SessionSummary, now: number) =>
  Date.parse(s.deadline) > now && s.stage !== "booked";

export function pipeline(list: SessionSummary[]) {
  const reached = STAGES.map((stage, i) => ({
    stage,
    reached: list.filter((s) => Math.max(0, stageIndex(s.stage)) >= i).length,
    now: list.filter((s) => s.stage === stage).length,
  }));
  return {
    reached,
    expired: list.filter((s) => s.exception === "expired").length,
    paused: list.filter((s) => s.paused || s.exception === "session_paused").length,
    otherExceptions: list.filter(
      (s) => s.exception && s.exception !== "expired" && s.exception !== "session_paused",
    ).length,
  };
}

export function kpis(list: SessionSummary[], details: LooseDetail[]) {
  const quoted = list.filter(isQuotedOrBeyond).length;
  const booked = list.filter((s) => s.stage === "booked").length;
  const attention = list.filter(needsPerson).length;

  // Quote totals in EUR: list value when the backend sends it, else the loaded detail.
  const byId = new Map(details.map((d) => [d.id, d]));
  let eur = 0;
  let eurCount = 0;
  for (const s of list) {
    const d = byId.get(s.id);
    const total = s.quote_total ?? d?.quote?.total ?? null;
    const currency = s.quote_total ? s.quote_currency : d?.quote?.currency;
    const n = total !== null ? Number(total) : NaN;
    if (currency === "EUR" && Number.isFinite(n)) {
      eur += n;
      eurCount += 1;
    }
  }
  return {
    received: list.length,
    quoted,
    booked,
    winRate: quoted > 0 ? booked / quoted : null,
    attention,
    eur,
    eurCount,
  };
}

export type ResponseSample = { id: string; ms: number; receivedAt: number };

export function responseTimes(details: LooseDetail[]): ResponseSample[] {
  const out: ResponseSample[] = [];
  for (const d of details) {
    const rec = d.request?.received_at ? Date.parse(d.request.received_at) : NaN;
    const dec = d.quote?.decided_at ? Date.parse(d.quote.decided_at) : NaN;
    if (Number.isFinite(rec) && Number.isFinite(dec) && dec >= rec) {
      out.push({ id: d.id, ms: dec - rec, receivedAt: rec });
    }
  }
  return out.sort((a, b) => a.receivedAt - b.receivedAt);
}

export function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? (s[mid] ?? null) : ((s[mid - 1] ?? 0) + (s[mid] ?? 0)) / 2;
}

/** Response-time points per the challenge (15 max): full ≤2 min, linear to zero at 6 min. */
export function responsePoints(ms: number): number {
  if (ms <= RESPONSE_FULL_MS) return 15;
  if (ms >= RESPONSE_ZERO_MS) return 0;
  return 15 * (1 - (ms - RESPONSE_FULL_MS) / (RESPONSE_ZERO_MS - RESPONSE_FULL_MS));
}

/** Counts by key, largest first, the tail past `keep` folded into "Other". */
export function countBy<T>(items: T[], key: (t: T) => string | null | undefined, keep = 5) {
  const map = new Map<string, number>();
  for (const it of items) {
    const k = key(it);
    if (k) map.set(k, (map.get(k) ?? 0) + 1);
  }
  const sorted = [...map.entries()].sort((a, b) => b[1] - a[1]);
  const head = sorted.slice(0, keep).map(([label, value]) => ({ label, value }));
  const rest = sorted.slice(keep).reduce((sum, [, v]) => sum + v, 0);
  return rest > 0 ? [...head, { label: "Other", value: rest }] : head;
}

export function usageTotals(rows: UsageRow[]) {
  const models = new Map<string, number>();
  let spend = 0;
  let calls = 0;
  let estimatedOnly = false;
  for (const r of rows) {
    const observed = r.observed_cost !== null ? Number(r.observed_cost) : NaN;
    const estimated = r.estimated_cost !== null ? Number(r.estimated_cost) : NaN;
    const cost = Number.isFinite(observed) ? observed : Number.isFinite(estimated) ? estimated : 0;
    if (!Number.isFinite(observed) && Number.isFinite(estimated)) estimatedOnly = true;
    spend += cost;
    calls += r.calls;
    models.set(r.model, (models.get(r.model) ?? 0) + cost);
  }
  return {
    spend,
    calls,
    estimatedOnly,
    models: [...models.entries()].sort((a, b) => b[1] - a[1]).map(([label, value]) => ({ label, value })),
  };
}
