/** Operator plumbing for the Intervene panel: guarded calls, the token prompt, inline confirms. */
import { useCallback, useEffect, useRef, useState } from "react";
import { type ApiError, parseApiError } from "./model";

type Pending = { label: string; fn: () => Promise<unknown> };
export type CallError = ApiError & { label: string };

/**
 * Runs an operator command. A 401 parks the command until a token is entered, then retries it.
 * Other failures are kept as an error the panel shows inline.
 */
export function useOperatorCall(onDone?: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<CallError | null>(null);
  const [needsToken, setNeedsToken] = useState<Pending | null>(null);
  const doneRef = useRef(onDone);
  doneRef.current = onDone;

  const run = useCallback(async (label: string, fn: () => Promise<unknown>): Promise<boolean> => {
    setBusy(label);
    setError(null);
    try {
      await fn();
      setNeedsToken(null);
      doneRef.current?.();
      return true;
    } catch (err) {
      const parsed = parseApiError(err);
      if (parsed.status === 401) setNeedsToken({ label, fn });
      else setError({ ...parsed, label });
      return false;
    } finally {
      setBusy(null);
    }
  }, []);

  const retryWithToken = useCallback(
    (token: string) => {
      try {
        localStorage.setItem("operator_token", token);
      } catch {
        /* storage blocked: the retry below will 401 again and say so */
      }
      if (needsToken) void run(needsToken.label, needsToken.fn);
    },
    [needsToken, run],
  );

  return {
    busy,
    error,
    needsToken,
    run,
    retryWithToken,
    clearError: () => setError(null),
    cancelToken: () => setNeedsToken(null),
  };
}

/** Human wording for a failed operator command. */
export function describeError(e: CallError): string {
  if (e.status === 404) return `“${e.label}” is not available on this backend build yet. Nothing was changed.`;
  if (e.code === "take_over_first") return "Take over the session first, then try again.";
  if (e.code === "edited_quote_changes_price")
    return "The edit was refused: the quoted total must stay in the text, the quote goes out under the stored price.";
  if (e.status === 403) return `Refused by the server: ${e.message}`;
  if (e.status === null) return `Could not reach the server: ${e.message}`;
  return `${e.label} failed: ${e.message}`;
}

export function TokenPrompt({
  label,
  onSubmit,
  onCancel,
}: {
  label: string;
  onSubmit: (token: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => inputRef.current?.focus(), []);
  return (
    <form
      className="ss-token"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim()) onSubmit(value.trim());
      }}
    >
      <p className="ss-token-title">Operator token needed</p>
      <p className="muted ss-small">
        “{label}” needs the operator token. It is kept in this browser only and sent with operator commands.
      </p>
      <label className="visually-hidden" htmlFor="ss-token-input">
        Operator token
      </label>
      <div className="ss-token-row">
        <input
          id="ss-token-input"
          ref={inputRef}
          className="input"
          type="password"
          autoComplete="off"
          placeholder="Operator token"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" className="btn btn-primary" disabled={!value.trim()}>
          Save and retry
        </button>
        <button type="button" className="btn btn-link" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/** An inline "are you sure" that replaces window.confirm. Focus lands on the confirm button. */
export function ConfirmInline({
  message,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  message: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => ref.current?.focus(), []);
  return (
    <div
      className={`ss-confirm${danger ? " is-danger" : ""}`}
      role="group"
      aria-label="Confirm"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
    >
      <p>{message}</p>
      <div className="ss-confirm-row">
        <button
          ref={ref}
          type="button"
          className={`btn btn-sm ${danger ? "btn-danger" : "btn-primary"}`}
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? "Working…" : confirmLabel}
        </button>
        <button type="button" className="btn btn-sm" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </div>
  );
}
