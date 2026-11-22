import type { ReactNode } from "react";
import "./charts.css";
import { defaultFormat, seriesColor } from "./shared";

export type DonutDatum = {
  label: string;
  value: number;
  /** CSS colour, e.g. "var(--series-3)". Defaults to the series colour at this index. */
  color?: string;
};

export type DonutProps = {
  data: DonutDatum[];
  /** Accessible name of the chart, e.g. "Carrier mix". */
  label: string;
  /** Big number in the hole. Defaults to the total. */
  centerValue?: ReactNode;
  /** Small caption under the big number. */
  centerLabel?: string;
  /** Ring thickness as a share of the radius (0..1). */
  thickness?: number;
  formatValue?: (value: number) => string;
  /** Show the legend with values and shares (default true). */
  legend?: boolean;
};

/**
 * Ring chart for parts of a whole (up to ~6 parts; fold the rest into "Other").
 * The legend lists every part with value and share, so colour is never the only signal.
 */
export function Donut({
  data,
  label,
  centerValue,
  centerLabel,
  thickness = 0.28,
  formatValue = defaultFormat,
  legend = true,
}: DonutProps) {
  const total = data.reduce((sum, d) => sum + Math.max(0, d.value), 0);
  const r = 40;
  const stroke = r * thickness * 1.6;
  const circ = 2 * Math.PI * r;
  const gap = data.filter((d) => d.value > 0).length > 1 ? 1.2 : 0;
  const pct = (v: number) => (total > 0 ? Math.round((v / total) * 100) : 0);

  let offset = 0;
  const segments = data.map((d, i) => {
    const len = total > 0 ? (Math.max(0, d.value) / total) * circ : 0;
    const seg = {
      ...d,
      color: d.color ?? seriesColor(i),
      dash: Math.max(0, len - gap),
      offset,
    };
    offset += len;
    return seg;
  });

  const summary = `${label}: ${data
    .map((d) => `${d.label} ${formatValue(d.value)} (${pct(d.value)}%)`)
    .join(", ")}`;

  return (
    <div className="ch ch-donut">
      <div className="ch-donut-ring">
        <svg viewBox="0 0 100 100" role="img" aria-label={summary}>
          <circle cx="50" cy="50" r={r} fill="none" stroke="var(--tint)" strokeWidth={stroke} />
          <g transform="rotate(-90 50 50)" className="ch-fade">
            {segments.map((s) =>
              s.dash > 0 ? (
                <circle
                  key={s.label}
                  className="ch-seg"
                  cx="50"
                  cy="50"
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={stroke}
                  strokeDasharray={`${s.dash} ${circ - s.dash}`}
                  strokeDashoffset={-s.offset}
                >
                  <title>{`${s.label}: ${formatValue(s.value)} (${pct(s.value)}%)`}</title>
                </circle>
              ) : null,
            )}
          </g>
        </svg>
        <div className="ch-donut-center" aria-hidden="true">
          <strong>{centerValue ?? formatValue(total)}</strong>
          {centerLabel ? <span>{centerLabel}</span> : null}
        </div>
      </div>
      {legend ? (
        <ul className="ch-legend">
          {segments.map((s) => (
            <li key={s.label}>
              <span className="ch-swatch" style={{ background: s.color }} aria-hidden="true" />
              <span className="ch-legend-label">{s.label}</span>
              <span className="ch-legend-value">{formatValue(s.value)}</span>
              <span className="ch-legend-share">{pct(s.value)}%</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
