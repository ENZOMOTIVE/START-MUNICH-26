import { useEffect, useId, useState } from "react";
import "./operator-token.css";

const KEY = "operator_token";

function readToken(): string {
  try {
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

function writeToken(value: string) {
  try {
    if (value) localStorage.setItem(KEY, value);
    else localStorage.removeItem(KEY);
  } catch {
    // Storage blocked: the token simply is not kept.
  }
}

/**
 * Small inline panel to enter the operator token. It is kept in this browser only
 * (`localStorage.operator_token`) and sent by the API client as `X-Operator-Token`.
 * `needed` opens the panel and explains why (the API answered 401).
 */
export function OperatorToken({ needed = false }: { needed?: boolean }) {
  const [saved, setSaved] = useState(readToken);
  const [draft, setDraft] = useState(saved);
  const [open, setOpen] = useState(needed);
  const inputId = useId();

  useEffect(() => {
    if (needed) setOpen(true);
  }, [needed]);

  const save = (value: string) => {
    const clean = value.trim();
    writeToken(clean);
    setSaved(clean);
    setDraft(clean);
    setOpen(false);
  };

  if (!open) {
    return (
      <div className="rv-token">
        <span className={`chip ${saved ? "chip-positive" : ""}`}>
          <span className="dot" aria-hidden="true" />
          {saved ? "Operator token set" : "No operator token"}
        </span>
        <button className="btn btn-sm" type="button" onClick={() => setOpen(true)}>
          {saved ? "Change" : "Add token"}
        </button>
      </div>
    );
  }

  return (
    <form
      className={`rv-token rv-token-open ${needed ? "rv-token-needed" : ""}`}
      onSubmit={(event) => {
        event.preventDefault();
        save(draft);
      }}
    >
      {needed && (
        <p className="rv-token-why" role="alert">
          The API asked for an operator token (401). Enter it to act on items.
        </p>
      )}
      <label className="rv-token-label" htmlFor={inputId}>
        Operator token
      </label>
      <div className="rv-token-row">
        <input
          id={inputId}
          className="input rv-token-input"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="Paste token"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="btn btn-sm btn-accent" type="submit">
          Save
        </button>
        {saved && (
          <button className="btn btn-sm" type="button" onClick={() => save("")}>
            Clear
          </button>
        )}
        {!needed && (
          <button
            className="btn btn-sm btn-link"
            type="button"
            onClick={() => {
              setDraft(saved);
              setOpen(false);
            }}
          >
            Cancel
          </button>
        )}
      </div>
      <p className="rv-token-note subtle">Kept in this browser only.</p>
    </form>
  );
}
