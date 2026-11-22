/** Pure helpers for the Sessions screens: shapes, stages, filters, error parsing. */
import type { SessionDetail, SessionEvent, SessionSummary } from "../../types/session";

/* ---------- loose shapes the backend sends beyond the shared hand-written types ---------- */

export type RequestView = {
  origin?: string;
  destination?: string;
  weight_kg?: string | null;
  volume_m3?: string | null;
  pickup_required?: boolean | null;
  pickup_zone?: string | null;
  service_intent?: string | null;
  received_at?: string | null;
  expires_at?: string | null;
  thread_id?: string | null;
  status?: string | null;
};

export type Charge = { code: string; amount: string; currency?: string; unit?: string };

export type OfferView = {
  carrier?: string | null;
  carrier_code?: string | null;
  total?: string | null;
  currency?: string | null;
  transit_days?: number | null;
  rate_id?: string | null;
  valid_until?: string | null;
  charges?: Charge[] | null;
  source_ref?: string | null;
  source_version?: string | null;
};

export type Candidate = {
  carrier?: string;
  total?: string;
  currency?: string;
  transit_days?: number | null;
  rate_id?: string;
  result?: string;
};

export type QuoteView = {
  total?: string;
  currency?: string;
  decided_at?: string | null;
  evidence?: { rule?: string; objective?: string; candidate_assessment?: Candidate[] } | null;
};

export type BookingView = {
  status?: string;
  booking_reference?: string | null;
  reference?: string | null;
  team_reference?: string | null;
  rate_id?: string | null;
  total?: string | null;
  currency?: string | null;
};

export type DetailView = {
  id: string;
  stage: string;
  paused: boolean;
  exception: string | null;
  deadline: string;
  simulated?: boolean;
  request: RequestView | null;
  offer: OfferView | null;
  quote: QuoteView | null;
  booking: BookingView | null;
  events: SessionEvent[];
};

export function viewOf(session: SessionDetail): DetailView {
  const raw = session as unknown as Record<string, unknown>;
  return {
    id: session.id,
    stage: String(session.stage),
    paused: session.paused === true,
    exception: session.exception ?? null,
    deadline: session.deadline,
    simulated: session.simulated,
    request: ((raw.request ?? raw.rfq) as RequestView | undefined) ?? null,
    offer: (raw.selected_offer as OfferView | undefined) ?? null,
    quote: (raw.quote as QuoteView | undefined) ?? null,
    booking: (raw.booking as BookingView | undefined) ?? null,
    events: session.events ?? [],
  };
}

/* ---------- stages ---------- */

export const STAGES = ["intake", "pricing", "quoted", "accepted", "booked"] as const;
export type Stage = (typeof STAGES)[number];

export function stageIndex(stage: string): number {
  return STAGES.indexOf(stage as Stage);
}

/** Exceptions that mean the request is over without a sale (not a call for help). */
const LOST_EXCEPTIONS = new Set(["expired", "offer_expired", "answered_elsewhere"]);

export function isLost(s: SessionSummary, now: number): boolean {
  if (s.exception && LOST_EXCEPTIONS.has(s.exception)) return true;
  const early = s.stage === "intake" || s.stage === "pricing";
  return early && !s.paused && !s.exception && msUntil(s.deadline, now) <= 0;
}

export function needsAttention(s: SessionSummary, now: number): boolean {
  if (isLost(s, now)) return false;
  return s.paused || Boolean(s.exception);
}

export function isLive(s: SessionSummary, now: number): boolean {
  return s.stage !== "booked" && !isLost(s, now) && msUntil(s.deadline, now) > 0;
}

export function msUntil(iso: string | null | undefined, now: number): number {
  if (!iso) return Number.NaN;
  return new Date(iso).getTime() - now;
}

/* ---------- list filtering ---------- */

export const FILTERS = [
  { id: "all", label: "All" },
  { id: "live", label: "Live" },
  { id: "attention", label: "Needs attention" },
  { id: "quoted", label: "Quoted" },
  { id: "booked", label: "Booked" },
  { id: "lost", label: "Lost / expired" },
] as const;
export type FilterId = (typeof FILTERS)[number]["id"];

export function matchesFilter(s: SessionSummary, filter: FilterId, now: number): boolean {
  switch (filter) {
    case "all":
      return true;
    case "live":
      return isLive(s, now);
    case "attention":
      return needsAttention(s, now);
    case "quoted":
      return s.stage === "quoted";
    case "booked":
      return s.stage === "booked";
    case "lost":
      return isLost(s, now);
  }
}

export function laneOf(s: { origin?: string | null; destination?: string | null; rfq?: { origin?: string; destination?: string } }) {
  return {
    origin: s.origin ?? s.rfq?.origin ?? "—",
    destination: s.destination ?? s.rfq?.destination ?? "—",
  };
}

/** Every word of the query must match one field: id prefix, lane, carrier, thread id, stage. */
export function matchesQuery(s: SessionSummary, query: string): boolean {
  const words = query
    .toLowerCase()
    .replace(/(→|->|-|\/|>)/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return true;
  const rfq = (s.rfq ?? {}) as Record<string, unknown>;
  const lane = laneOf(s);
  const fields = [
    s.id,
    lane.origin,
    lane.destination,
    s.carrier ?? "",
    typeof rfq.thread_id === "string" ? rfq.thread_id : "",
    s.stage,
    s.exception ?? "",
  ].map((f) => String(f).toLowerCase());
  return words.every((w) =>
    fields.some((f, i) => (i === 0 || i === 4 ? f.startsWith(w) : f.includes(w))),
  );
}

export type SortId = "deadline" | "newest";

/** Deadline soonest: open windows first (soonest first), then closed ones (latest closed first). */
export function sortSessions(rows: SessionSummary[], sort: SortId, now: number, attentionFirst: boolean) {
  const t = (s: SessionSummary) => new Date(s.deadline).getTime() || 0;
  return [...rows].sort((a, b) => {
    if (attentionFirst) {
      const d = Number(needsAttention(b, now)) - Number(needsAttention(a, now));
      if (d) return d;
    }
    if (sort === "newest") return t(b) - t(a);
    const aOpen = t(a) > now;
    const bOpen = t(b) > now;
    if (aOpen !== bOpen) return aOpen ? -1 : 1;
    return aOpen ? t(a) - t(b) : t(b) - t(a);
  });
}

/* ---------- errors from api/client ("<status> <text>: <body>") ---------- */

export type ApiError = { status: number | null; message: string; code?: string };

export function parseApiError(err: unknown): ApiError {
  const raw = err instanceof Error ? err.message : String(err);
  const match = /^(\d{3})\s[^:]*(?::\s*([\s\S]*))?$/.exec(raw);
  if (!match) return { status: null, message: raw };
  const status = Number(match[1]);
  const body = match[2] ?? "";
  try {
    const json = JSON.parse(body) as { error?: { code?: string; message?: string }; detail?: unknown };
    if (json.error?.message) return { status, message: json.error.message, code: json.error.code };
    if (typeof json.detail === "string") return { status, message: json.detail };
  } catch {
    /* plain-text body */
  }
  return { status, message: body || raw };
}

export function errorStatus(message: string | null): number | null {
  if (!message) return null;
  const m = /^(\d{3})\s/.exec(message);
  return m ? Number(m[1]) : null;
}

/* ---------- small formatters ---------- */

/** Like duration(), but keeps sub-second answers honest instead of rounding them to 0s. */
export function preciseDuration(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  if (ms < 0) return "—";
  if (ms < 1000) return `${Math.max(1, Math.round(ms))} ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

export function countdown(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString("en-GB", {
        day: "2-digit",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
}
