import { useState } from "react";
import { admitSession, pauseSession, resumeSession } from "../../api/client";
import { humanize } from "../../lib/format";
import { ActionsPanel } from "./ActionsPanel";
import { Icon } from "./bits";
import type { DetailView } from "./model";
import { ConfirmInline, describeError, TokenPrompt, useOperatorCall } from "./operator";

/** Where a person steps in: take over, hand back, admit, and the outbound mail. */
export function IntervenePanel({ view, onChanged }: { view: DetailView; onChanged: () => void }) {
  const op = useOperatorCall(onChanged);
  const [confirming, setConfirming] = useState<"resume" | "admit" | null>(null);
  const finished = view.stage === "booked";

  const state = view.paused
    ? { tone: "dark", title: "You are in control", text: "The agents are paused on this request. Nothing goes out unless you send it." }
    : view.exception
      ? {
          tone: "alert",
          title: "Needs a person",
          text: `The agents stopped: ${humanize(view.exception)}. Take over to look at it.`,
        }
      : finished
        ? { tone: "calm", title: "Done", text: "Booked. You can still take over to look at the mail." }
        : { tone: "calm", title: "Agents are handling this", text: "Take over at any moment. They pause right away." };

  return (
    <aside className="ss-intervene" aria-labelledby="ss-int-h">
      <div className={`ss-int-state ss-int-${state.tone}`}>
        <h2 id="ss-int-h" className="ss-int-title">
          <Icon name={view.paused ? "hand" : view.exception ? "alert" : "plane"} size={18} /> {state.title}
        </h2>
        <p className="ss-small">{state.text}</p>
      </div>

      <div className="ss-int-controls">
        {!view.paused ? (
          <button
            type="button"
            className="btn btn-primary ss-int-main"
            disabled={op.busy !== null}
            onClick={() => void op.run("Take over", () => pauseSession(view.id))}
          >
            <Icon name="hand" size={16} /> {op.busy === "Take over" ? "Taking over…" : "Take over"}
          </button>
        ) : confirming === "resume" ? (
          <ConfirmInline
            message="Hand back to the agents? They continue at once and may send mail on their own."
            confirmLabel="Hand back"
            busy={op.busy === "Hand back"}
            onConfirm={() =>
              void op.run("Hand back", () => resumeSession(view.id)).then(() => setConfirming(null))
            }
            onCancel={() => setConfirming(null)}
          />
        ) : (
          <button
            type="button"
            className="btn btn-accent ss-int-main"
            disabled={op.busy !== null}
            onClick={() => setConfirming("resume")}
          >
            <Icon name="play" size={16} /> Hand back to agents
          </button>
        )}

        {confirming === "admit" ? (
          <ConfirmInline
            message="Admit this request's thread to the bounded live run and release it? In live mode the agents may then send real mail for it."
            confirmLabel="Admit"
            danger
            busy={op.busy === "Admit to live run"}
            onConfirm={() =>
              void op.run("Admit to live run", () => admitSession(view.id)).then(() => setConfirming(null))
            }
            onCancel={() => setConfirming(null)}
          />
        ) : (
          <button
            type="button"
            className="btn btn-sm ss-int-secondary"
            disabled={op.busy !== null}
            onClick={() => setConfirming("admit")}
          >
            Admit to live run
          </button>
        )}
      </div>

      <div aria-live="polite">
        {op.needsToken && (
          <TokenPrompt label={op.needsToken.label} onSubmit={op.retryWithToken} onCancel={op.cancelToken} />
        )}
        {op.error && (
          <div className={op.error.status === 404 ? "ss-note" : "error-box ss-small"} role={op.error.status === 404 ? undefined : "alert"}>
            {describeError(op.error)}{" "}
            <button type="button" className="btn btn-link ss-small" onClick={op.clearError}>
              Dismiss
            </button>
          </div>
        )}
      </div>

      <ActionsPanel
        sessionId={view.id}
        paused={view.paused}
        quoteTotal={view.quote?.total ?? null}
        run={op.run}
        busy={op.busy}
      />
    </aside>
  );
}
