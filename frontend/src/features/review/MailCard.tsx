import { useState } from "react";
import { humanize, money } from "../../lib/format";
import { mailKind, type ReviewMail } from "./queue";

type Resolver = (externalId: string, approve: boolean) => Promise<string | null>;

const KIND_LABEL = {
  unmatched: "Unmatched mail",
  tariff: "Tariff file",
  bid: "Carrier bid",
  other: "Mail",
} as const;

const KIND_CHIP = {
  unmatched: "chip-info",
  tariff: "chip-warning",
  bid: "chip-dark",
  other: "",
} as const;

/** What each action does, worded for the inline confirm. Matches the backend's resolve semantics:
 * approve=true prices with a waiting bid; approve=false clears the flag (for a tariff it lifts the hold). */
function actionsFor(mail: ReviewMail) {
  const kind = mailKind(mail);
  if (kind === "bid") {
    return [
      { approve: true, label: "Approve bid", confirm: "Approve this bid and price with it?", accent: true },
      { approve: false, label: "Dismiss", confirm: "Dismiss this bid without using it?", accent: false },
    ];
  }
  if (kind === "tariff") {
    return [
      {
        approve: false,
        label: "Approve tariff",
        confirm: "Tariff checked: lift the hold and reprice this carrier's open offers?",
        accent: true,
      },
    ];
  }
  return [{ approve: false, label: "Mark handled", confirm: "Mark this mail as handled?", accent: false }];
}

export function MailCard({ mail, onResolve }: { mail: ReviewMail; onResolve: Resolver }) {
  const kind = mailKind(mail);
  const actions = actionsFor(mail);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = confirming === null ? null : actions[confirming];

  const run = async () => {
    if (!pending) return;
    setBusy(true);
    setError(null);
    const problem = await onResolve(mail.external_id, pending.approve);
    setBusy(false);
    if (problem) setError(problem);
    else setConfirming(null);
  };

  return (
    <article className={`rv-mail rv-mail-${kind}`} aria-label={`${KIND_LABEL[kind]}: ${mail.subject}`}>
      <header className="rv-mail-head">
        <span className={`chip ${KIND_CHIP[kind]}`}>{KIND_LABEL[kind]}</span>
        {mail.kind && <span className="chip">{humanize(mail.kind)}</span>}
      </header>
      <h3 className="rv-mail-subject">{mail.subject || "(no subject)"}</h3>
      <p className="rv-mail-from muted">
        From <span className="rv-mail-sender">{mail.sender}</span>
      </p>

      {mail.unmatched && (
        <p className="rv-mail-reason">
          <span className="subtle">Why it waits:</span> {mail.unmatched.reason}
        </p>
      )}

      {mail.tariff && (
        <div className="rv-mail-block">
          <p className="rv-mail-reason">
            New tariff file. This carrier's prices are on hold until a person approves it.
          </p>
          <ul className="rv-files">
            {mail.tariff.files.map((file) => (
              <li key={file.filename} className="rv-file">
                <span className="rv-file-icon" aria-hidden="true" />
                <span className="rv-file-name">{file.filename}</span>
                <span className={`chip ${file.status === "review_required" ? "chip-warning" : ""}`}>
                  {humanize(file.status)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {mail.bid && (
        <div className="rv-bid">
          <div>
            <span className="rv-bid-label subtle">{mail.bid.carrier} offers</span>
            <span className="rv-bid-amount num">
              {mail.bid.amount === null
                ? "Amount not stated"
                : mail.bid.currency
                  ? money(mail.bid.amount, mail.bid.currency)
                  : mail.bid.amount}
            </span>
            {mail.bid.amount !== null && !mail.bid.currency && (
              <span className="subtle rv-bid-note">Currency not stated</span>
            )}
          </div>
          {mail.bid.reasons.length > 0 && (
            <div>
              <span className="rv-bid-label subtle">Not used automatically because</span>
              <ul className="rv-reasons">
                {mail.bid.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <footer className="rv-mail-actions">
        {pending ? (
          <div className="rv-confirm" role="group" aria-label="Confirm action">
            <p className="rv-confirm-q">{pending.confirm}</p>
            <div className="rv-confirm-btns">
              <button
                className={`btn btn-sm ${pending.accent ? "btn-accent" : "btn-primary"}`}
                type="button"
                disabled={busy}
                onClick={() => void run()}
              >
                {busy ? "Working…" : `Yes, ${pending.label.toLowerCase()}`}
              </button>
              <button
                className="btn btn-sm"
                type="button"
                disabled={busy}
                onClick={() => {
                  setConfirming(null);
                  setError(null);
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          actions.map((action, index) => (
            <button
              key={action.label}
              className={`btn btn-sm ${action.accent ? "btn-accent" : ""}`}
              type="button"
              onClick={() => setConfirming(index)}
            >
              {action.label}
            </button>
          ))
        )}
        {error && (
          <p className="rv-mail-error" role="alert">
            {error}
          </p>
        )}
      </footer>
    </article>
  );
}
