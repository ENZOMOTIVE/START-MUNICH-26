import "./charts.css";
import { defaultFormat } from "./shared";

export type FunnelStage = {
  key: string;
  label: string;
  /** How many items reached this stage (or are in it, depending on what you pass). */
  value: number;
  /** Optional caption under the count, e.g. "12 now". Defaults to share of the first stage. */
  caption?: string;
  color?: string;
};

export type StageFunnelProps = {
  stages: FunnelStage[];
  /** Accessible name, e.g. "Pipeline from intake to booked". */
  label: string;
  formatValue?: (value: number) => string;
};

/**
 * Ordered stage bars, each scaled to the first stage, with the count and a caption
 * as text. Use it for conversion funnels or any ordered pipeline.
 */
export function StageFunnel({ stages, label, formatValue = defaultFormat }: StageFunnelProps) {
  const base = Math.max(0, ...stages.map((s) => s.value));
  const first = stages[0]?.value ?? 0;
  return (
    <ol className="ch ch-funnel" aria-label={label}>
      {stages.map((s, i) => {
        const pct = base > 0 ? (s.value / base) * 100 : 0;
        const share = first > 0 ? Math.round((s.value / first) * 100) : 0;
        return (
          <li key={s.key} className="ch-funnel-row">
            <span className="ch-funnel-label">{s.label}</span>
            <div className="ch-funnel-bar" aria-hidden="true">
              <div
                className="ch-funnel-fill ch-grow-x"
                style={{
                  width: `${pct}%`,
                  background: s.color ?? "var(--series-1)",
                  transformOrigin: "left center",
                  animationDelay: `${i * 70}ms`,
                }}
              />
            </div>
            <span className="ch-funnel-count">
              <strong>{formatValue(s.value)}</strong>
              <span>{s.caption ?? `${share}% of ${stages[0]?.label.toLowerCase() ?? "first"}`}</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
