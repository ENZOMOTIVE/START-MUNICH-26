import { clock, RESPONSE_FULL_MS, RESPONSE_ZERO_MS } from "../../lib/format";
import { countdown, type DetailView, preciseDuration } from "./model";

/** Response points the challenge gives for an answer after `ms` (15 at ≤2 min, 0 at ≥6 min). */
function responsePoints(ms: number): number {
  if (ms <= RESPONSE_FULL_MS) return 15;
  if (ms >= RESPONSE_ZERO_MS) return 0;
  return 15 * (1 - (ms - RESPONSE_FULL_MS) / (RESPONSE_ZERO_MS - RESPONSE_FULL_MS));
}

/**
 * The 6-minute window from the request to its expiry, with the scoring bands and
 * the moment the quote went out.
 */
export function DeadlineClock({ view, now }: { view: DetailView; now: number }) {
  const received = view.request?.received_at ? new Date(view.request.received_at).getTime() : Number.NaN;
  const expires = new Date(view.request?.expires_at ?? view.deadline).getTime();
  const start = Number.isFinite(received) ? received : expires - RESPONSE_ZERO_MS;
  const span = Math.max(1, expires - start);
  const pct = (t: number) => `${Math.min(100, Math.max(0, ((t - start) / span) * 100))}%`;

  const decided = view.quote?.decided_at ? new Date(view.quote.decided_at).getTime() : Number.NaN;
  const answered = Number.isFinite(decided);
  const answerMs = decided - start;
  const left = expires - now;
  const open = left > 0;

  let headline: string;
  let sub: string;
  let tone: "ok" | "warn" | "bad" | "closed";
  if (answered) {
    headline = `Answered in ${preciseDuration(answerMs)}`;
    const pts = responsePoints(answerMs);
    sub = `≈ ${pts.toFixed(1)} of 15 response-time points (derived from the challenge rule)`;
    tone = answerMs <= RESPONSE_FULL_MS ? "ok" : answerMs < RESPONSE_ZERO_MS ? "warn" : "bad";
  } else if (open) {
    headline = `${countdown(left)} left`;
    const elapsed = now - start;
    sub =
      elapsed <= RESPONSE_FULL_MS
        ? `Full response points for ${countdown(RESPONSE_FULL_MS - elapsed)} more`
        : "Past 2 min: response points are sliding to zero";
    tone = elapsed <= RESPONSE_FULL_MS ? "ok" : "warn";
  } else {
    headline = "Expired without a quote";
    sub = `The window closed at ${clock(new Date(expires).toISOString())}`;
    tone = "closed";
  }

  const fullEnd = start + RESPONSE_FULL_MS;
  return (
    <section className="card ss-clock" aria-labelledby="ss-clock-h">
      <div className="card-head">
        <div>
          <h2 id="ss-clock-h" className="card-title">
            Response clock
          </h2>
          <p className="card-sub">6-minute window from request to expiry</p>
        </div>
        {!answered && open && <span className="chip chip-warning">Open</span>}
      </div>
      <p className={`ss-clock-big num ss-tone-${tone}`} aria-live="off">
        {headline}
      </p>
      <p className="muted ss-clock-sub">{sub}</p>

      <div className="ss-track" role="img" aria-label={`Timeline: request at ${clock(new Date(start).toISOString())}, full points until 2 minutes, zero at 6 minutes${answered ? `, quote sent after ${preciseDuration(answerMs)}` : ""}.`}>
        <span className="ss-band ss-band-full" style={{ left: 0, width: pct(fullEnd) }} />
        <span className="ss-band ss-band-slide" style={{ left: pct(fullEnd), right: 0 }} />
        {open && !answered && <span className="ss-track-elapsed" style={{ width: pct(now) }} />}
        {answered && (
          <span className="ss-marker ss-marker-quote" style={{ left: pct(decided) }}>
            <span className="ss-marker-label">Quote sent</span>
          </span>
        )}
        {open && (
          <span className="ss-marker ss-marker-now" style={{ left: pct(now) }}>
            <span className="ss-marker-label">Now</span>
          </span>
        )}
      </div>
      <div className="ss-track-scale subtle num" aria-hidden="true">
        <span>0:00 · {clock(new Date(start).toISOString())}</span>
        <span style={{ left: pct(fullEnd) }}>2:00</span>
        <span>6:00 · {clock(new Date(expires).toISOString())}</span>
      </div>
      <ul className="ss-legend subtle">
        <li>
          <span className="ss-swatch ss-band-full" /> ≤ 2 min: full points
        </li>
        <li>
          <span className="ss-swatch ss-band-slide" /> 2–6 min: sliding
        </li>
        <li>≥ 6 min: zero, request expires</li>
      </ul>
    </section>
  );
}
