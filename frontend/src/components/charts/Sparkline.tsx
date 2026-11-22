import { useState } from "react";
import "./charts.css";
import { defaultFormat, useWidth } from "./shared";

export type SparklineProps = {
  values: number[];
  /** Accessible name, e.g. "Response time per quote, oldest to newest". */
  label: string;
  /** Optional label per point for the hover readout (same length as values). */
  pointLabels?: string[];
  /** Line colour (CSS). Default var(--series-1). */
  color?: string;
  /** Fill the area under the line (default true). */
  area?: boolean;
  /** Height in px (default 56). */
  height?: number;
  /** Horizontal reference line, e.g. a target. */
  reference?: { value: number; label: string };
  /** Fixed y-range; defaults to [0, max of values and reference]. */
  domain?: [number, number];
  formatValue?: (value: number) => string;
};

/**
 * Small trend line (with optional area and reference line). Hover shows the nearest
 * point. Fewer than two points renders nothing but the text alternative.
 */
export function Sparkline({
  values,
  label,
  pointLabels,
  color = "var(--series-1)",
  area = true,
  height = 56,
  reference,
  domain,
  formatValue = defaultFormat,
}: SparklineProps) {
  const [ref, width] = useWidth<HTMLDivElement>(240);
  const [hover, setHover] = useState<number | null>(null);
  const last = values[values.length - 1];
  const summary =
    values.length > 0
      ? `${label}: ${values.length} points, latest ${formatValue(last ?? 0)}, low ${formatValue(
          Math.min(...values),
        )}, high ${formatValue(Math.max(...values))}`
      : `${label}: no data`;

  if (values.length < 2) {
    return (
      <div className="ch" ref={ref}>
        <span className="visually-hidden">{summary}</span>
      </div>
    );
  }

  const pad = 4;
  const [lo, hi] = domain ?? [0, Math.max(...values, reference?.value ?? 0) || 1];
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - 2 * pad);
  const y = (v: number) => pad + (1 - (Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo || 1)) * (height - 2 * pad);
  const line = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const fill = `${line} L${x(values.length - 1)},${height - pad} L${x(0)},${height - pad} Z`;
  const hv = hover !== null ? values[hover] : undefined;

  return (
    <div className="ch" ref={ref}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={summary}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const rel = ((e.clientX - box.left) / box.width) * width;
          const i = Math.round(((rel - pad) / (width - 2 * pad)) * (values.length - 1));
          setHover(Math.min(values.length - 1, Math.max(0, i)));
        }}
        onMouseLeave={() => setHover(null)}
      >
        {area ? <path d={fill} fill={color} opacity={0.14} className="ch-fade" /> : null}
        {reference ? (
          <line
            x1={0}
            x2={width}
            y1={y(reference.value)}
            y2={y(reference.value)}
            stroke="var(--fg-3)"
            strokeDasharray="3 3"
            strokeWidth={1}
          >
            <title>{reference.label}</title>
          </line>
        ) : null}
        <path
          d={line}
          fill="none"
          stroke={color}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          pathLength={1}
          className="ch-draw"
        />
        {hover !== null && hv !== undefined ? (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={0} y2={height} stroke="var(--fg-3)" strokeWidth={1} />
            <circle cx={x(hover)} cy={y(hv)} r={4} fill={color} stroke="var(--surface)" strokeWidth={2} />
          </g>
        ) : last !== undefined ? (
          <circle cx={x(values.length - 1)} cy={y(last)} r={3.5} fill={color} stroke="var(--surface)" strokeWidth={2} />
        ) : null}
      </svg>
      {hover !== null && hv !== undefined ? (
        <div className="ch-tooltip" style={{ left: x(hover), top: y(hv) }} aria-hidden="true">
          {pointLabels?.[hover] ? `${pointLabels[hover]} · ` : ""}
          {formatValue(hv)}
        </div>
      ) : null}
    </div>
  );
}
