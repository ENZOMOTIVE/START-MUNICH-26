import { Link } from "react-router-dom";
import "./charts.css";
import { defaultFormat } from "./shared";

export type BarListItem = {
  /** Stable key; defaults to the label. */
  key?: string;
  label: string;
  value: number;
  /** Optional muted text after the label, e.g. "42%". */
  hint?: string;
  /** In-app route; the label becomes a link. */
  to?: string;
  /** CSS colour of the bar. Defaults to the first series colour. */
  color?: string;
};

export type BarListProps = {
  items: BarListItem[];
  /** Accessible name of the list, e.g. "Top lanes by RFQ count". */
  label: string;
  /** Scale maximum; defaults to the largest value. */
  max?: number;
  formatValue?: (value: number) => string;
};

/**
 * Ranked horizontal bars with the label and value as real text above each bar.
 * Plain HTML, so it reflows at any width and reads naturally in a screen reader.
 */
export function BarList({ items, label, max, formatValue = defaultFormat }: BarListProps) {
  const top = max ?? Math.max(0, ...items.map((i) => i.value));
  return (
    <ul className="ch ch-barlist" aria-label={label}>
      {items.map((item, i) => {
        const pct = top > 0 ? Math.min(100, (item.value / top) * 100) : 0;
        return (
          <li key={item.key ?? item.label} className="ch-barlist-row">
            <div className="ch-barlist-top">
              <span style={{ minWidth: 0, display: "flex" }}>
                {item.to ? (
                  <Link className="ch-barlist-label" to={item.to}>
                    {item.label}
                  </Link>
                ) : (
                  <span className="ch-barlist-label">{item.label}</span>
                )}
                {item.hint ? <span className="ch-barlist-hint">{item.hint}</span> : null}
              </span>
              <span className="ch-barlist-value">{formatValue(item.value)}</span>
            </div>
            <div className="ch-track" aria-hidden="true">
              <div
                className="ch-fill ch-grow-x"
                style={{
                  width: `${pct}%`,
                  background: item.color ?? "var(--series-1)",
                  animationDelay: `${i * 50}ms`,
                }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
