import { clock } from "../../lib/format";
import type { SessionEvent } from "../../types/session";
import { type DetailView, type Stage, STAGES, stageIndex } from "./model";

/** Which event types mark the start of a stage. The earliest matching event wins. */
const STAGE_EVENTS: Record<Stage, string[]> = {
  intake: ["message_received", "rfq_extracted"],
  pricing: ["rates_loaded", "carrier_enquiry_sent", "carrier_bid", "offer_selected"],
  quoted: ["quote_recorded"],
  accepted: ["customer_accepted"],
  booked: ["booking_confirmed"],
};

const LABELS: Record<Stage, string> = {
  intake: "Request read",
  pricing: "Pricing",
  quoted: "Quoted",
  accepted: "Accepted",
  booked: "Booked",
};

function stageTimes(view: DetailView, events: SessionEvent[]): Partial<Record<Stage, string>> {
  const out: Partial<Record<Stage, string>> = {};
  const sorted = [...events].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at));
  for (const stage of STAGES) {
    const hit = sorted.find((e) => STAGE_EVENTS[stage].includes(e.type));
    if (hit) out[stage] = hit.occurred_at;
  }
  // Fall back to the record's own timestamps when the timeline does not carry the event.
  out.intake ??= view.request?.received_at ?? undefined;
  out.quoted ??= view.quote?.decided_at ?? undefined;
  return out;
}

export function StageStepper({ view, events }: { view: DetailView; events: SessionEvent[] }) {
  const at = stageIndex(view.stage);
  const times = stageTimes(view, events);
  return (
    <ol className="ss-stepper" aria-label="Progress">
      {STAGES.map((stage, i) => {
        const state = i < at ? "done" : i === at ? "now" : "todo";
        const when = times[stage];
        return (
          <li key={stage} className={`ss-step is-${state}`} aria-current={state === "now" ? "step" : undefined}>
            <span className="ss-step-dot" aria-hidden="true">
              {state === "done" ? "✓" : i + 1}
            </span>
            <span className="ss-step-label">{LABELS[stage]}</span>
            <span className="ss-step-time subtle num">
              {when && state !== "todo" ? clock(when) : state === "todo" ? "pending" : "—"}
            </span>
            <span className="visually-hidden">
              {state === "done" ? "done" : state === "now" ? "current stage" : "not reached"}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
