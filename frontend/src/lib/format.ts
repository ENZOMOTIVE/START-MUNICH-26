/** Shared formatting for money, times and countdowns. Pure functions, no React. */

export function money(value: string | number | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${n.toFixed(2)} ${currency}`;
  }
}

export function usd(value: number, digits = 4): string {
  return `$${value.toFixed(digits)}`;
}

export function kg(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? `${new Intl.NumberFormat("en-GB").format(n)} kg` : String(value);
}

/** "4m 12s", "38s", "2h 05m". Negative input is treated as zero. */
export function duration(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

export function timeAgo(iso: string, now = Date.now()): string {
  const diff = now - new Date(iso).getTime();
  if (!Number.isFinite(diff)) return "—";
  if (diff < 0) return `in ${duration(-diff)}`;
  if (diff < 5000) return "just now";
  return `${duration(diff)} ago`;
}

export function clock(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "—"
    : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** "rate_candidates_ready" -> "Rate candidates ready" */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const s = value.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Challenge scoring windows: full time points within 2 min of the RFQ, zero at 6 min. */
export const RESPONSE_FULL_MS = 2 * 60_000;
export const RESPONSE_ZERO_MS = 6 * 60_000;
/** LLM cost points: full at no spend, zero at 0.10 USD per accepted quote. */
export const COST_ZERO_USD = 0.1;
