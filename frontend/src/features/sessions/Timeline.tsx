import type { ReactNode } from "react";
import { clock, humanize, money, timeAgo } from "../../lib/format";
import type { SessionEvent } from "../../types/session";
import { Icon } from "./bits";

type Look = { icon: string; tone: "info" | "positive" | "warning" | "negative" | "neutral"; title: string };

const LOOKS: Record<string, Look> = {
  message_received: { icon: "mail", tone: "neutral", title: "Request email received" },
  mail_routed: { icon: "route", tone: "neutral", title: "Mail routed" },
  rfq_extracted: { icon: "scan", tone: "info", title: "Request read" },
  model_check: { icon: "scan", tone: "info", title: "Model check" },
  clarification_sent: { icon: "send", tone: "warning", title: "Asked the customer to clarify" },
  clarification_resolved: { icon: "check", tone: "positive", title: "Clarification answered" },
  rates_loaded: { icon: "coins", tone: "info", title: "Rates loaded" },
  carrier_enquiry_sent: { icon: "send", tone: "info", title: "Carriers asked for bids" },
  carrier_bid: { icon: "coins", tone: "info", title: "Carrier bid in" },
  offer_selected: { icon: "check", tone: "info", title: "Offer selected" },
  quote_recorded: { icon: "send", tone: "positive", title: "Quote sent" },
  quote_blocked: { icon: "alert", tone: "negative", title: "Quote blocked" },
  customer_replied: { icon: "mail", tone: "neutral", title: "Customer replied" },
  customer_accepted: { icon: "handshake", tone: "positive", title: "Customer accepted" },
  acceptance_held: { icon: "pause", tone: "warning", title: "Acceptance held" },
  booking_submitted: { icon: "box", tone: "info", title: "Booking submitted" },
  booking_confirmed: { icon: "box", tone: "positive", title: "Booking confirmed" },
  tracking_update: { icon: "plane", tone: "info", title: "Tracking update" },
  tracking_answered: { icon: "plane", tone: "positive", title: "Tracking question answered" },
  paused: { icon: "hand", tone: "warning", title: "Taken over by a person" },
  session_paused: { icon: "hand", tone: "warning", title: "Paused for a person" },
  resumed: { icon: "play", tone: "positive", title: "Handed back to the agents" },
  send_resolved: { icon: "check", tone: "positive", title: "Uncertain send resolved" },
  exception: { icon: "alert", tone: "negative", title: "Needs a person" },
};

function lookOf(type: string): Look {
  return LOOKS[type] ?? { icon: "dot", tone: "neutral", title: humanize(type) };
}

function str(v: unknown): string | null {
  return typeof v === "string" || typeof v === "number" ? String(v) : null;
}

/** One line that says what happened, from the fields most events carry. */
function summaryOf(e: SessionEvent): string {
  const p = e.payload ?? {};
  const bits: string[] = [];
  if (str(p.origin) && str(p.destination)) bits.push(`${p.origin} → ${p.destination}`);
  if (str(p.weight_kg)) bits.push(`${p.weight_kg} kg`);
  if (str(p.service_intent)) bits.push(humanize(str(p.service_intent)));
  if (str(p.carrier)) bits.push(String(p.carrier));
  if (str(p.total)) bits.push(money(str(p.total), str(p.currency) ?? "EUR"));
  if (str(p.count)) bits.push(`${p.count} found`);
  if (str(p.code)) bits.push(humanize(str(p.code)));
  if (str(p.booking_reference)) bits.push(String(p.booking_reference));
  if (str(p.by_operator)) bits.push(`operator says: ${humanize(str(p.by_operator))}`);
  return bits.join(" · ");
}

function Value({ value, depth }: { value: unknown; depth: number }): ReactNode {
  if (value === null || value === undefined || value === "") return <span className="subtle">none</span>;
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "string" || typeof value === "number") return <span className="ss-break">{String(value)}</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="subtle">none</span>;
    if (depth >= 1 || value.some((v) => typeof v === "object" && v !== null)) {
      return <span className="subtle">{value.length} items</span>;
    }
    return <span className="ss-break">{value.map(String).join(", ")}</span>;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (depth >= 1) return <span className="subtle">{entries.length} fields</span>;
    return <Fields data={value as Record<string, unknown>} depth={depth + 1} />;
  }
  return String(value);
}

function Fields({ data, depth }: { data: Record<string, unknown>; depth: number }) {
  const entries = Object.entries(data).filter(([k]) => k !== "simulated");
  if (!entries.length) return <span className="subtle">No details.</span>;
  return (
    <dl className="kv ss-fields">
      {entries.map(([k, v]) => (
        <div key={k} className="ss-field">
          <dt>{humanize(k)}</dt>
          <dd>
            <Value value={v} depth={depth} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Newest first. The list is a polite live region so a screen reader hears new events. */
export function Timeline({ events, now, live }: { events: SessionEvent[]; now: number; live: boolean }) {
  const ordered = [...events].sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));
  return (
    <section className="card ss-timeline" aria-labelledby="ss-tl-h">
      <div className="card-head">
        <div>
          <h2 id="ss-tl-h" className="card-title">
            Live timeline
          </h2>
          <p className="card-sub">Newest first · {events.length} events</p>
        </div>
        {live && (
          <span className="chip chip-positive">
            <span className="dot dot-live" aria-hidden="true" /> Streaming
          </span>
        )}
      </div>
      {ordered.length === 0 ? (
        <p className="muted">Nothing has happened yet. Events appear here as the agents work.</p>
      ) : (
        <ol className="ss-events" aria-live="polite" aria-relevant="additions">
          {ordered.map((e) => {
            const look = lookOf(e.type);
            const summary = summaryOf(e);
            const payload = e.payload ?? {};
            const hasDetails = Object.keys(payload).some((k) => k !== "simulated");
            return (
              <li key={e.id} className={`ss-event ss-ev-${look.tone}`}>
                <span className="ss-event-icon">
                  <Icon name={look.icon} size={16} />
                </span>
                <div className="ss-event-body">
                  <div className="ss-event-head">
                    <span className="ss-event-title">{look.title}</span>
                    <time className="subtle num" dateTime={e.occurred_at} title={e.occurred_at}>
                      {clock(e.occurred_at)} · {timeAgo(e.occurred_at, now)}
                    </time>
                  </div>
                  {summary && <p className="muted ss-event-sum">{summary}</p>}
                  {hasDetails && (
                    <details className="ss-disclosure ss-event-details">
                      <summary>Details</summary>
                      <Fields data={payload} depth={0} />
                    </details>
                  )}
                  {e.simulated && <span className="ss-event-sim subtle">simulated</span>}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
