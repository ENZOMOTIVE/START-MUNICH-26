import "./charts.css";
import { defaultFormat } from "./shared";

export type MeterProps = {
  label: string;
  value: number;
  max: number;
  formatValue?: (value: number) => string;
  /** Share (0..1) from which the fill turns to the warning colour (default 0.8). */
  warnAt?: number;
};

/**
 * Linear "used of limit" meter. Uses the native meter semantics via role="meter",
 * and the numbers are always printed, so state never depends on colour alone.
 */
export function Meter({ label, value, max, formatValue = defaultFormat, warnAt = 0.8 }: MeterProps) {
  const share = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const color =
    share >= 1 ? "var(--negative)" : share >= warnAt ? "var(--warning)" : "var(--series-1)";
  return (
    <div className="ch ch-meter">
      <div className="ch-meter-top">
        <span>{label}</span>
        <span>
          {formatValue(value)} / {formatValue(max)}
        </span>
      </div>
      <div
        className="ch-track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={value}
        aria-valuetext={`${formatValue(value)} of ${formatValue(max)}`}
      >
        <div className="ch-fill ch-grow-x" style={{ width: `${share * 100}%`, background: color }} />
      </div>
    </div>
  );
}
