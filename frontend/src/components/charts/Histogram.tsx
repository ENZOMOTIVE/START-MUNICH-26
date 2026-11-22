import { useState } from "react";
import "./charts.css";
import { useWidth } from "./shared";

export type HistogramBand = {
  /** Inclusive lower bound, in the same unit as the values. */
  from: number;
  /** Exclusive upper bound. Use Infinity for "and above". */
  to: number;
  /** CSS colour for bars in this band and its faint background. */
  color: string;
  /** Short band name shown above the plot and in tooltips, e.g. "Full points". */
  label: string;
};

export type HistogramMarker = { value: number; label: string };

export type HistogramProps = {
  values: number[];
  /** Accessible name, e.g. "Quote response times". */
  label: string;
  /** Domain start (default 0). */
  min?: number;
  /** Domain end. Values at or above it land in the last bin, labelled "max+". */
  max: number;
  binSize: number;
  bands?: HistogramBand[];
  /** Vertical reference lines, e.g. the median. */
  markers?: HistogramMarker[];
  /** Formats axis ticks and bin ranges. */
  formatTick?: (value: number) => string;
  /** Plot height in px (default 200). */
  height?: number;
  /** Tick every n-th bin edge (default: every 2nd bin). */
  tickEvery?: number;
};

const PAD = { top: 28, right: 8, bottom: 26, left: 28 };

/**
 * Binned distribution with optional threshold bands behind the bars and reference
 * markers. Bars take their band's colour; the band names are printed as text, and a
 * visually-hidden table lists every bin for screen readers.
 */
export function Histogram({
  values,
  label,
  min = 0,
  max,
  binSize,
  bands = [],
  markers = [],
  formatTick = (v) => String(v),
  height = 200,
  tickEvery = 2,
}: HistogramProps) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  const binCount = Math.max(1, Math.ceil((max - min) / binSize));
  const bins = Array.from({ length: binCount }, (_, i) => ({
    from: min + i * binSize,
    to: min + (i + 1) * binSize,
    count: 0,
  }));
  for (const v of values) {
    const idx = Math.min(binCount - 1, Math.max(0, Math.floor((v - min) / binSize)));
    const bin = bins[idx];
    if (bin) bin.count += 1;
  }
  const peak = Math.max(1, ...bins.map((b) => b.count));
  const innerW = Math.max(40, width - PAD.left - PAD.right);
  const innerH = height - PAD.top - PAD.bottom;
  const x = (v: number) => PAD.left + ((Math.min(v, max) - min) / (max - min)) * innerW;
  const y = (c: number) => PAD.top + innerH - (c / peak) * innerH;
  const bandOf = (v: number) => bands.find((b) => v >= b.from && v < b.to);
  const binW = innerW / binCount;
  const yTicks = peak <= 4 ? Array.from({ length: peak + 1 }, (_, i) => i) : [0, Math.round(peak / 2), peak];
  const rangeText = (b: { from: number; to: number }, i: number) =>
    i === binCount - 1 ? `${formatTick(b.from)}+` : `${formatTick(b.from)}–${formatTick(b.to)}`;

  const hovered = hover !== null ? bins[hover] : undefined;

  return (
    <div className="ch" ref={ref}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`${label}: ${values.length} values in ${binCount} bins`}
        onMouseLeave={() => setHover(null)}
      >
        {bands.map((b) => {
          const x0 = x(Math.max(b.from, min));
          const x1 = x(Math.min(b.to, max));
          if (x1 <= x0) return null;
          return (
            <g key={b.label}>
              <rect x={x0} y={PAD.top} width={x1 - x0} height={innerH} fill={b.color} opacity={0.14} />
              <text className="ch-axis-text" x={x0 + 6} y={PAD.top - 10} style={{ fontWeight: 600 }}>
                {b.label}
              </text>
            </g>
          );
        })}
        {yTicks.map((t) => (
          <g key={t}>
            <line className="ch-grid" x1={PAD.left} x2={PAD.left + innerW} y1={y(t)} y2={y(t)} />
            <text className="ch-axis-text" x={PAD.left - 8} y={y(t) + 4} textAnchor="end">
              {t}
            </text>
          </g>
        ))}
        {bins.map((b, i) => {
          const h = (b.count / peak) * innerH;
          const color = bandOf(b.from)?.color ?? "var(--series-1)";
          const bx = PAD.left + i * binW + 1;
          const bw = Math.max(1, binW - 2);
          return (
            <g key={i} className="ch-bar-group" onMouseEnter={() => setHover(i)}>
              {b.count > 0 ? (
                <path
                  className="ch-bar ch-grow-y"
                  style={{ animationDelay: `${i * 25}ms` }}
                  d={roundedTop(bx, PAD.top + innerH - h, bw, h, Math.min(4, bw / 2))}
                  fill={color}
                  stroke="var(--fg)"
                  strokeOpacity={0.18}
                  strokeWidth={0.75}
                />
              ) : null}
              <rect className="ch-bar-hit" x={PAD.left + i * binW} y={PAD.top} width={binW} height={innerH}>
                <title>{`${rangeText(b, i)}: ${b.count}`}</title>
              </rect>
            </g>
          );
        })}
        <line
          x1={PAD.left}
          x2={PAD.left + innerW}
          y1={PAD.top + innerH}
          y2={PAD.top + innerH}
          stroke="var(--fg-3)"
          strokeWidth={1}
        />
        {bins.map((b, i) =>
          i % tickEvery === 0 ? (
            <text
              key={`t${i}`}
              className="ch-axis-text"
              x={PAD.left + i * binW}
              y={height - 8}
              textAnchor={i === 0 ? "start" : "middle"}
            >
              {formatTick(b.from)}
            </text>
          ) : null,
        )}
        <text className="ch-axis-text" x={PAD.left + innerW} y={height - 8} textAnchor="end">
          {formatTick(max)}+
        </text>
        {markers.map((m) => {
          const mx = x(m.value);
          return (
            <g key={m.label} className="ch-fade">
              <line
                x1={mx}
                x2={mx}
                y1={PAD.top - 2}
                y2={PAD.top + innerH}
                stroke="var(--fg)"
                strokeWidth={2}
                strokeDasharray="4 3"
              />
              <text
                className="ch-strong-text"
                x={mx + (mx > PAD.left + innerW - 90 ? -6 : 6)}
                y={PAD.top + 12}
                textAnchor={mx > PAD.left + innerW - 90 ? "end" : "start"}
              >
                {m.label}
              </text>
            </g>
          );
        })}
      </svg>
      {hovered && hover !== null ? (
        <div
          className="ch-tooltip"
          style={{ left: PAD.left + hover * binW + binW / 2, top: y(hovered.count) }}
          aria-hidden="true"
        >
          {rangeText(hovered, hover)}: {hovered.count}
          {bandOf(hovered.from) ? ` · ${bandOf(hovered.from)?.label}` : ""}
        </div>
      ) : null}
      <table className="visually-hidden">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">Range</th>
            <th scope="col">Count</th>
            <th scope="col">Band</th>
          </tr>
        </thead>
        <tbody>
          {bins.map((b, i) => (
            <tr key={i}>
              <td>{rangeText(b, i)}</td>
              <td>{b.count}</td>
              <td>{bandOf(b.from)?.label ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Bar path with rounded top corners, square at the baseline. */
function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.min(r, h);
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`;
}
