import { useState } from "react";
import { editAction, listActions, resolveAction, sendAction, type SessionAction } from "../../api/client";
import { humanize } from "../../lib/format";
import { usePolling } from "../../lib/usePolling";
import { Icon } from "./bits";
import { errorStatus, parseApiError } from "./model";
import { ConfirmInline } from "./operator";

type Run = (label: string, fn: () => Promise<unknown>) => Promise<boolean>;

const STATUS_LOOK: Record<string, { label: string; chip: string }> = {
  pending: { label: "Draft, not sent", chip: "chip-warning" },
  failed: { label: "Send failed", chip: "chip-negative" },
  UNKNOWN: { label: "Delivery unknown", chip: "chip-negative" },
  sent: { label: "Sent", chip: "chip-positive" },
  confirmed: { label: "Delivered", chip: "chip-positive" },
};

/**
 * Outbound mail of one session: what went out, what waits, what is uncertain.
 * Editing, sending and resolving only work while a person has taken the session over.
 */
export function ActionsPanel({
  sessionId,
  paused,
  quoteTotal,
  run,
  busy,
}: {
  sessionId: string;
  paused: boolean;
  quoteTotal: string | null;
  run: Run;
  busy: string | null;
}) {
  const [missing, setMissing] = useState(false);
  const { data, error, loading, refresh } = usePolling(
    () => listActions(sessionId),
    missing ? 60_000 : 3000,
    `${sessionId}:${missing}`,
  );
  if (!missing && errorStatus(error) === 404) setMissing(true);

  // Text being typed is kept here, apart from the polled list, so a reload never wipes it.
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [confirming, setConfirming] = useState<string | null>(null);

  const act = async (label: string, fn: () => Promise<unknown>, after?: () => void) => {
    const ok = await run(label, fn);
    if (ok) {
      after?.();
      setConfirming(null);
    }
    await refresh();
  };

  let body;
  if (missing || errorStatus(error) === 404) {
    body = (
      <p className="ss-note">
        <Icon name="mail" size={16} /> This backend build does not list outbound mail yet. Pause and hand back still work.
      </p>
    );
  } else if (loading && !data) {
    body = (
      <div aria-busy="true">
        <span className="skeleton" style={{ display: "block", height: 56, marginBottom: 8 }} />
        <span className="skeleton" style={{ display: "block", height: 56 }} />
      </div>
    );
  } else if (error && !data) {
    body = <p className="error-box ss-small">Could not load outbound mail: {parseApiError(error).message}</p>;
  } else if (!data || data.length === 0) {
    body = <p className="muted ss-small">No outbound mail for this request yet.</p>;
  } else {
    body = (
      <ul className="ss-actions">
        {data.map((a) => (
          <ActionItem
            key={a.id}
            action={a}
            paused={paused}
            quoteTotal={quoteTotal}
            draft={drafts[a.id]}
            setDraft={(text) =>
              setDrafts((d) => {
                const next = { ...d };
                if (text === null) delete next[a.id];
                else next[a.id] = text;
                return next;
              })
            }
            confirming={confirming}
            setConfirming={setConfirming}
            busy={busy}
            act={act}
            sessionId={sessionId}
          />
        ))}
      </ul>
    );
  }

  return (
    <section className="ss-actions-wrap" aria-labelledby="ss-actions-h">
      <h3 id="ss-actions-h" className="ss-h3">
        Outbound mail
      </h3>
      {body}
    </section>
  );
}

function ActionItem({
  action: a,
  paused,
  quoteTotal,
  draft,
  setDraft,
  confirming,
  setConfirming,
  busy,
  act,
  sessionId,
}: {
  action: SessionAction;
  paused: boolean;
  quoteTotal: string | null;
  draft: string | undefined;
  setDraft: (text: string | null) => void;
  confirming: string | null;
  setConfirming: (key: string | null) => void;
  busy: string | null;
  act: (label: string, fn: () => Promise<unknown>, after?: () => void) => Promise<void>;
  sessionId: string;
}) {
  const look = STATUS_LOOK[a.delivery_status] ?? { label: humanize(a.delivery_status), chip: "" };
  const isDraft = a.delivery_status === "pending" || a.delivery_status === "failed";
  const isUnknown = a.delivery_status === "UNKNOWN";
  const text = draft ?? a.body ?? "";
  const dirty = draft !== undefined && draft !== (a.body ?? "");
  const isQuote = a.kind === "first_quote";
  const losesTotal = isQuote && Boolean(quoteTotal) && !text.includes(quoteTotal as string);
  const title = humanize(a.purpose ?? a.kind);
  const fieldId = `ss-draft-${a.id}`;

  const send = () =>
    act(
      "Send",
      async () => {
        if (dirty) await editAction(sessionId, a.id, text);
        await sendAction(sessionId, a.id);
      },
      () => setDraft(null),
    );

  return (
    <li className={`ss-action${isUnknown ? " is-unknown" : ""}`}>
      <div className="ss-action-head">
        <span className={`chip ${look.chip}`}>{look.label}</span>
        <span className="ss-action-title">{title}</span>
      </div>
      <p className="subtle ss-small ss-break">
        {a.to ? `To ${a.to}` : "Recipient not given"}
        {a.edited_by_operator ? " · edited by an operator" : ""}
        {a.simulated ? " · simulated" : ""}
      </p>
      {a.subject && <p className="ss-action-subject">{a.subject}</p>}

      {a.body !== null &&
        (isDraft && paused ? (
          <>
            <label className="visually-hidden" htmlFor={fieldId}>
              Draft text for {title}
            </label>
            <textarea
              id={fieldId}
              className="textarea ss-draft"
              rows={8}
              value={text}
              aria-describedby={losesTotal ? `${fieldId}-warn` : undefined}
              aria-invalid={losesTotal || undefined}
              onChange={(e) => setDraft(e.target.value)}
            />
            {losesTotal && (
              <p id={`${fieldId}-warn`} className="ss-warn ss-small" role="status">
                Keep the quoted total <strong className="num">{quoteTotal}</strong> in the text. The quote goes out
                under the stored price, so the server refuses an edit without it.
              </p>
            )}
          </>
        ) : (
          <pre className="ss-mail-body">{a.body}</pre>
        ))}

      {(isDraft || isUnknown) && !paused && (
        <p className="ss-note ss-small">
          <Icon name="hand" size={14} /> Take over to {isUnknown ? "resolve" : "edit or send"} this.
        </p>
      )}

      {paused && isDraft && (
        <div className="ss-action-btns">
          {a.body !== null && (
            <button
              type="button"
              className="btn btn-sm"
              disabled={!dirty || losesTotal || busy !== null}
              onClick={() => void act("Save draft", () => editAction(sessionId, a.id, text), () => setDraft(null))}
            >
              {busy === "Save draft" ? "Saving…" : "Save draft"}
            </button>
          )}
          {dirty && (
            <button type="button" className="btn btn-sm btn-link" onClick={() => setDraft(null)}>
              Discard edits
            </button>
          )}
          {confirming !== `send:${a.id}` && (
            <button
              type="button"
              className="btn btn-sm btn-accent"
              disabled={losesTotal || busy !== null}
              onClick={() => setConfirming(`send:${a.id}`)}
            >
              <Icon name="send" size={14} /> {dirty ? "Save and send" : "Approve and send"}
            </button>
          )}
        </div>
      )}
      {confirming === `send:${a.id}` && (
        <ConfirmInline
          message={`Send this ${a.simulated ? "simulated " : ""}email${a.to ? ` to ${a.to}` : ""} now? It goes out through the same outbox as the agents.`}
          confirmLabel="Send now"
          busy={busy === "Send"}
          onConfirm={() => void send()}
          onCancel={() => setConfirming(null)}
        />
      )}

      {paused && isUnknown && (
        <div className="ss-unknown">
          <p className="ss-small">
            Nobody knows whether this reached the recipient. It is never resent automatically. Check the mailbox, then
            say what you found.
          </p>
          {confirming === `yes:${a.id}` || confirming === `no:${a.id}` ? (
            <ConfirmInline
              message={
                confirming === `yes:${a.id}`
                  ? "Record it as delivered? It will be marked confirmed."
                  : "Record it as not delivered? It goes back to a draft you can send again."
              }
              confirmLabel="Record it"
              busy={busy === "Resolve"}
              onConfirm={() =>
                void act("Resolve", () => resolveAction(sessionId, a.id, confirming === `yes:${a.id}`))
              }
              onCancel={() => setConfirming(null)}
            />
          ) : (
            <div className="ss-action-btns">
              <button type="button" className="btn btn-sm" onClick={() => setConfirming(`yes:${a.id}`)}>
                It was delivered
              </button>
              <button type="button" className="btn btn-sm" onClick={() => setConfirming(`no:${a.id}`)}>
                It was not delivered
              </button>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
