import { Link } from "react-router-dom";
import type { ReviewQueue } from "../../api/client";
import { humanize } from "../../lib/format";

type BlockedSession = ReviewQueue["sessions"][number];

const STAGE_ORDER = ["intake", "pricing", "quoted", "accepted", "booked"];

/** Blocked sessions, newest first, each linking to its session page. */
export function SessionList({ sessions }: { sessions: BlockedSession[] }) {
  const rows = [...sessions].reverse();
  return (
    <ul className="rv-sessions">
      {rows.map((session) => (
        <li key={session.id}>
          <Link className="rv-session" to={`/sessions/${session.id}`}>
            <span className="rv-session-reason">
              <span className="rv-session-icon" aria-hidden="true">
                !
              </span>
              <span>
                <span className="rv-session-title">{humanize(session.reason)}</span>
                <span className="rv-session-id mono subtle">{session.id.slice(0, 8)}</span>
              </span>
            </span>
            <span className="rv-session-meta">
              <StageTrack stage={session.stage} />
              {session.paused && (
                <span className="chip chip-warning">
                  <span aria-hidden="true">❚❚</span> Paused
                </span>
              )}
              <span className="rv-session-open" aria-hidden="true">
                →
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/** Five small steps with the reached ones filled, plus the stage name as text. */
function StageTrack({ stage }: { stage: string }) {
  const index = STAGE_ORDER.indexOf(stage);
  return (
    <span className="rv-stage">
      {index >= 0 && (
        <span className="rv-stage-steps" aria-hidden="true">
          {STAGE_ORDER.map((name, i) => (
            <span key={name} className={`rv-stage-step ${i <= index ? "is-done" : ""}`} />
          ))}
        </span>
      )}
      <span className="rv-stage-name">{humanize(stage)}</span>
    </span>
  );
}
