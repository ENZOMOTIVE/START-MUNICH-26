import type { ReactNode } from "react";
import "./charts.css";

export type GaugeBand = {
  /** Upper bound of this band as a value on the gauge's scale. */
  to: number;
  color: string;
};

export type GaugeProps = {
  value: number;
  max: number;
  /** Accessible name, e.g. "LLM cost per quote against the $0.10 cap". */
  label: string;
  /** Big readout under the arc, e.g. "$0.012". */
  readout: ReactNode;
  /** Caption under the readout, e.g. "per quote · cap $0.10". */
  caption?: string;
  /** Text of the value for the accessible name. Defaults to String(value). */
  valueText?: string;
  /** Faint coloured scale bands behind the arc, in ascending order. */
  bands?: GaugeBand[];
  /** Colour of the value arc. Defaults to the colour of the band the value is in. */
  color?: string;
  /** Labels at the arc ends. */
  minLabel?: string;
  maxLabel?: string;
};

const R = 80;
const CX = 100;
const CY = 96;

function arcPath(): string {
  return `M${CX - R},${CY} A${R},${R} 0 0 1 ${CX + R},${CY}`;
}

/**
 * Semicircle gauge for one value against a cap. Values beyond `max` pin the arc
 * at the end; the readout still shows the true number.
 */
export function Gauge({
  value,
  max,
  label,
  readout,
  caption,
  valueText,
  bands = [],
  color,
  minLabel = "0",
  maxLabel,
}: GaugeProps) {
  const share = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const arcColor = color ?? bands.find((b) => value <= b.to)?.color ?? bands[bands.length - 1]?.color ?? "var(--series-1)";
  let prev = 0;
  return (
    <div className="ch ch-gauge">
      <svg viewBox="0 0 200 112" role="img" aria-label={`${label}: ${valueText ?? String(value)} of ${maxLabel ?? String(max)}`}>
        <path d={arcPath()} fill="none" stroke="var(--tint)" strokeWidth={16} strokeLinecap="round" pathLength={100} />
        {bands.map((b) => {
          const from = (prev / max) * 100;
          const to = (Math.min(b.to, max) / max) * 100;
          prev = b.to;
          return (
            <path
              key={b.to}
              d={arcPath()}
              fill="none"
              stroke={b.color}
              strokeOpacity={0.25}
              strokeWidth={16}
              pathLength={100}
              strokeDasharray={`0 ${from} ${Math.max(0, to - from)} 100`}
            />
          );
        })}
        <path
          className="ch-gauge-arc"
          d={arcPath()}
          fill="none"
          stroke={arcColor}
          strokeWidth={16}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={`${share * 100} 100`}
          opacity={share > 0 ? 1 : 0}
        />
        <text className="ch-axis-text" x={CX - R} y={CY + 16} textAnchor="middle">
          {minLabel}
        </text>
        <text className="ch-axis-text" x={CX + R} y={CY + 16} textAnchor="middle">
          {maxLabel ?? String(max)}
        </text>
      </svg>
      <div className="ch-gauge-readout" aria-hidden="true">
        <strong>{readout}</strong>
        {caption ? <span>{caption}</span> : null}
      </div>
    </div>
  );
}
