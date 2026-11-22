import { useState } from "react";
import "./charts.css";
import { defaultFormat, useWidth } from "./shared";

export type StackedSeries = { key: string; label: string; color: string };
export type StackedBucket = { label: string; values: Record<string, number> };

export type StackedBarsProps = {
  series: StackedSeries[];
  buckets: StackedBucket[];
  /** Accessible name, e.g. "RFQs received over time, by outcome". */
  label: string;
  height?: number;
};

/** Smallest "nice" y-axis step (1, 2, 5 x 10^n) that gives at most ~5 gridlines. */
function niceStep(max: number): number {
  const raw = Math.max(1, max) / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const f = raw / pow;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * pow;
}

/**
 * Stacked columns over time. Hover (or focus, via the hidden table) shows the split of
 * one column. Series stack bottom to top in the order given; the legend shows totals.
 */
export function StackedBars({ series, buckets, label, height = 240 }: StackedBarsProps) {
  const [ref, width] = useWidth<HTMLDivElement>(640);
  const [hover, setHover] = useState<number | null>(null);

  const totals = buckets.map((b) => series.reduce((s, x) => s + (b.values[x.key] ?? 0), 0));
  const grand = series.map((s) => buckets.reduce((sum, b) => sum + (b.values[s.key] ?? 0), 0));
  const summary = `${label}: ${totals.reduce((a, b) => a + b, 0)} in ${buckets.length} intervals`;

  const left = 34;
  const bottom = 24;
  const top = 8;
  const plotW = Math.max(40, width - left - 4);
  const plotH = height - top - bottom;
  const step = niceStep(Math.max(...totals, 1));
  const yMax = Math.ceil(Math.max(...totals, 1) / step) * step;
  const slot = plotW / Math.max(1, buckets.length);
  const barW = Math.max(3, Math.min(34, slot * 0.72));
  const y = (v: number) => top + plotH - (v / yMax) * plotH;
  const labelEvery = Math.max(1, Math.ceil(buckets.length / Math.max(2, Math.floor(plotW / 64))));
  const ticks: number[] = [];
  for (let v = 0; v <= yMax; v += step) ticks.push(v);

  const hb = hover !== null ? buckets[hover] : undefined;
  const hx = hover !== null ? left + slot * hover + slot / 2 : 0;

  return (
    <div>
      <div className="ch" ref={ref}>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={summary}
          onMouseLeave={() => setHover(null)}
        >
          {ticks.map((v) => (
            <g key={v}>
              <line className="ch-grid" x1={left} x2={width} y1={y(v)} y2={y(v)} />
              <text className="ch-axis-text" x={left - 8} y={y(v) + 4} textAnchor="end">
                {defaultFormat(v)}
              </text>
            </g>
          ))}
          {buckets.map((b, i) => {
            const cx = left + slot * i + slot / 2;
            let acc = 0;
            return (
              <g key={i} onMouseEnter={() => setHover(i)} onMouseMove={() => setHover(i)}>
                <rect
                  x={left + slot * i}
                  y={top}
                  width={slot}
                  height={plotH}
                  fill={hover === i ? "var(--tint-hover, rgba(127,127,127,0.1))" : "transparent"}
                />
                {series.map((s) => {
                  const v = b.values[s.key] ?? 0;
                  if (v <= 0) return null;
                  const y1 = y(acc + v);
                  const h = y(acc) - y1;
                  acc += v;
                  return (
                    <rect
                      key={s.key}
                      className="ch-bar"
                      x={cx - barW / 2}
                      width={barW}
                      style={{ y: y1, height: Math.max(1, h - 1) }}
                      rx={2}
                      fill={s.color}
                    />
                  );
                })}
                {i % labelEvery === 0 ? (
                  <text className="ch-axis-text" x={cx} y={height - 6} textAnchor="middle">
                    {b.label}
                  </text>
                ) : null}
              </g>
            );
          })}
        </svg>
        {hb ? (
          <div className="ch-tooltip" style={{ left: hx, top: y(totals[hover ?? 0] ?? 0) }} aria-hidden="true">
            {hb.label} · {totals[hover ?? 0]} total
            {series
              .filter((s) => (hb.values[s.key] ?? 0) > 0)
              .map((s) => ` · ${s.label} ${hb.values[s.key]}`)
              .join("")}
          </div>
        ) : null}
      </div>
      <ul className="ch-key" aria-hidden="true">
        {series.map((s, i) => (
          <li key={s.key}>
            <span className="ch-swatch" style={{ background: s.color }} />
            {s.label} <strong className="num">{grand[i]}</strong>
          </li>
        ))}
      </ul>
      <table className="visually-hidden">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th>Interval</th>
            {series.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((b, i) => (
            <tr key={i}>
              <td>{b.label}</td>
              {series.map((s) => (
                <td key={s.key}>{b.values[s.key] ?? 0}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
