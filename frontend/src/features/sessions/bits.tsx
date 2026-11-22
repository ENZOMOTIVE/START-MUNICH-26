/** Small visuals shared by the Sessions list and detail. Hand-built SVG, tokens only. */
import { humanize, RESPONSE_ZERO_MS } from "../../lib/format";
import { countdown, msUntil, STAGES, stageIndex } from "./model";

/** Compact 5-step progress: intake → pricing → quoted → accepted → booked. */
export function StageProgress({ stage }: { stage: string }) {
  const at = stageIndex(stage);
  const label = at >= 0 ? `Stage ${at + 1} of 5: ${stage}` : `Stage: ${humanize(stage)}`;
  return (
    <span className="ss-progress" role="img" aria-label={label} title={label}>
      {STAGES.map((s, i) => (
        <span
          key={s}
          className={`ss-progress-step${i < at ? " is-done" : ""}${i === at ? " is-now" : ""}`}
        />
      ))}
      <span className="ss-progress-label" aria-hidden="true">
        {humanize(stage)}
      </span>
    </span>
  );
}

/** Ring that empties over the 6-minute window. Past the deadline it reads "closed". */
export function DeadlineRing({ deadline, now, size = 44 }: { deadline: string; now: number; size?: number }) {
  const left = msUntil(deadline, now);
  const open = Number.isFinite(left) && left > 0;
  const fraction = open ? Math.min(1, left / RESPONSE_ZERO_MS) : 0;
  const r = size / 2 - 4;
  const c = 2 * Math.PI * r;
  const tone = !open ? "closed" : left < 60_000 ? "urgent" : left < 4 * 60_000 ? "warn" : "ok";
  const text = open ? `${countdown(left)} left to respond` : "Response window closed";
  return (
    <span className={`ss-ring ss-ring-${tone}`} role="img" aria-label={text} title={text}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} className="ss-ring-track" />
        {open && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            className="ss-ring-fill"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - fraction)}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        )}
      </svg>
      <span className="ss-ring-text num" aria-hidden="true">
        {open ? countdown(left) : "closed"}
      </span>
    </span>
  );
}

export function StatusChips({ paused, exception }: { paused: boolean; exception: string | null }) {
  return (
    <>
      {paused && (
        <span className="chip chip-dark">
          <Icon name="pause" size={12} /> Paused
        </span>
      )}
      {exception && exception !== "session_paused" && (
        <span className="chip chip-negative">
          <Icon name="alert" size={12} /> {humanize(exception)}
        </span>
      )}
    </>
  );
}

const PATHS: Record<string, string> = {
  mail: "M3 6h18v12H3z M3 7l9 6 9-6",
  scan: "M4 7V4h3 M17 4h3v3 M20 17v3h-3 M7 20H4v-3 M7 12h10",
  coins: "M12 4c4 0 7 1.3 7 3s-3 3-7 3-7-1.3-7-3 3-3 7-3z M5 7v5c0 1.7 3 3 7 3s7-1.3 7-3V7 M5 12v5c0 1.7 3 3 7 3s7-1.3 7-3v-5",
  check: "M5 12.5l4.5 4.5L19 7.5",
  send: "M3 11.5 21 4l-7.5 18-2.5-8-8-2.5z",
  handshake: "M3 12l4-4 5 3 5-3 4 4 M7 8v6l5 4 5-4V8",
  box: "M3 8l9-5 9 5v8l-9 5-9-5z M3 8l9 5 9-5 M12 13v8",
  alert: "M12 3 2 20h20L12 3z M12 10v4 M12 17v.5",
  pause: "M8 5v14 M16 5v14",
  play: "M7 4l13 8-13 8z",
  hand: "M8 12V5.5a1.5 1.5 0 0 1 3 0V11 M11 10V4.5a1.5 1.5 0 0 1 3 0V11 M14 10V5.5a1.5 1.5 0 0 1 3 0V14c0 4-2.5 7-6 7-2.5 0-4-1.5-5.5-3.5L3.5 14a1.5 1.5 0 0 1 2.3-2L8 14",
  plane: "M2.5 13.5 21 6l-4.5 13-4-5.5-4.5 3 .5-5.5-6-1.5z",
  dot: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  search: "M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z M20 20l-4-4",
  back: "M15 5l-7 7 7 7",
  route: "M6 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M18 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M8 17h7a3 3 0 0 0 0-6H9a3 3 0 0 1 0-6h7",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M12 7v5l3 2",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="ss-icon"
    >
      <path d={PATHS[name] ?? PATHS.dot} />
    </svg>
  );
}

export function Lane({ origin, destination, className = "" }: { origin: string; destination: string; className?: string }) {
  return (
    <span className={`ss-lane display ${className}`}>
      <span className="visually-hidden">{`${origin} to ${destination}`}</span>
      <span aria-hidden="true">{origin}</span>
      <span className="ss-lane-arrow" aria-hidden="true">
        →
      </span>
      <span aria-hidden="true">{destination}</span>
    </span>
  );
}
