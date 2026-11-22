import type { SandboxResult } from "../../api/client";
import { humanize } from "../../lib/format";
import { seconds } from "./format";

type Mark = { at: number; lane: number; label: string };

const LANES = ["Mail in", "Mail out", "Faults", "Errors"] as const;
const W = 1000;
const LEFT = 92;
const RIGHT = 16;
const LANE_H = 34;
const TOP = 10;

/** One row per lane, simple marks by simulated second. Shapes differ per lane so colour is not the only signal. */
export function RunTimeline({ result }: { result: SandboxResult }) {
  const marks: Mark[] = [];
  for (const [key, mails] of Object.entries(result.threads)) {
    for (const mail of mails) {
      marks.push({
        at: mail.at_s,
        lane: mail.direction === "in" ? 0 : 1,
        label: `${key}: ${mail.direction === "in" ? "received" : `sent${mail.purpose ? ` (${mail.purpose})` : ""}`} "${mail.subject}"`,
      });
    }
  }
  for (const fault of result.faults) {
    marks.push({ at: fault.at_s, lane: 2, label: `${humanize(fault.fault)} on ${fault.where}` });
  }
  for (const error of result.errors) {
    if (typeof error.at_s === "number") {
      marks.push({ at: error.at_s, lane: 3, label: `Error ${String(error.code ?? "")}`.trim() });
    }
  }
  if (marks.length === 0) return null;

  const end = Math.max(result.simulated_seconds, ...marks.map((m) => m.at), 1);
  const x = (at: number) => LEFT + (at / end) * (W - LEFT - RIGHT);
  const usedLanes = LANES.map((_, i) => i).filter((i) => i < 2 || marks.some((m) => m.lane === i));
  const laneY = (lane: number) => TOP + usedLanes.indexOf(lane) * LANE_H + LANE_H / 2;
  const height = TOP + usedLanes.length * LANE_H + 28;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * end);
  const sorted = [...marks].sort((a, b) => a.at - b.at);

  return (
    <section className="sb-section" aria-labelledby="sb-tl-h">
      <h2 id="sb-tl-h" className="sb-section-title">
        Simulated run
      </h2>
      <div className="card sb-timeline">
        <svg
          viewBox={`0 0 ${W} ${height}`}
          className="sb-timeline-svg"
          role="img"
          aria-label={`Timeline over ${seconds(end)} simulated: ${marks.filter((m) => m.lane === 0).length} mails in, ${marks.filter((m) => m.lane === 1).length} out, ${marks.filter((m) => m.lane === 2).length} faults, ${marks.filter((m) => m.lane === 3).length} errors. Full list below.`}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={TOP} y2={height - 24} className="sb-tl-grid" />
              <text x={x(t)} y={height - 8} className="sb-tl-tick" textAnchor={t === 0 ? "start" : t === end ? "end" : "middle"}>
                {seconds(t)}
              </text>
            </g>
          ))}
          {usedLanes.map((lane) => (
            <g key={lane}>
              <text x={0} y={laneY(lane) + 4} className="sb-tl-lane">
                {LANES[lane]}
              </text>
              <line x1={LEFT} x2={W - RIGHT} y1={laneY(lane)} y2={laneY(lane)} className="sb-tl-axis" />
            </g>
          ))}
          {marks.map((mark, index) => {
            const cx = x(mark.at);
            const cy = laneY(mark.lane);
            return (
              <g key={index} className={`sb-tl-mark sb-tl-lane-${mark.lane}`}>
                <title>{`${seconds(mark.at)} · ${mark.label}`}</title>
                {mark.lane === 0 && <circle cx={cx} cy={cy} r={7} />}
                {mark.lane === 1 && <rect x={cx - 6.5} y={cy - 6.5} width={13} height={13} rx={3} />}
                {mark.lane === 2 && <path d={`M${cx} ${cy - 8} L${cx + 8} ${cy + 6} L${cx - 8} ${cy + 6} Z`} />}
                {mark.lane === 3 && <path d={`M${cx - 6} ${cy - 6} L${cx + 6} ${cy + 6} M${cx + 6} ${cy - 6} L${cx - 6} ${cy + 6}`} />}
              </g>
            );
          })}
        </svg>
        <div className="sb-tl-legend" aria-hidden="true">
          <span><i className="sb-key sb-key-0" /> Mail in</span>
          <span><i className="sb-key sb-key-1" /> Mail out</span>
          {usedLanes.includes(2) && <span><i className="sb-key sb-key-2" /> Fault</span>}
          {usedLanes.includes(3) && <span><i className="sb-key sb-key-3" /> Error</span>}
        </div>
        <details className="sb-tl-details">
          <summary>Timeline as a list ({sorted.length} events)</summary>
          <ol className="sb-list">
            {sorted.map((mark, index) => (
              <li key={index}>
                <span className="num subtle">{seconds(mark.at)}</span> {LANES[mark.lane]}: {mark.label}
              </li>
            ))}
          </ol>
        </details>
      </div>
    </section>
  );
}
