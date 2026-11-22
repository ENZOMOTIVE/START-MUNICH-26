/** Simulated-time offset: "t+42 s", "t+6:05". */
export function seconds(value: number): string {
  const s = Math.max(0, Math.round(value));
  if (s < 60) return `t+${s} s`;
  const m = Math.floor(s / 60);
  return `t+${m}:${String(s % 60).padStart(2, "0")}`;
}

/** Any API value as short text. */
export function show(value: unknown): string {
  if (value === null || value === undefined) return "none";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
