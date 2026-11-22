import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getReview, resolveMail, type ReviewQueue } from "../../api/client";
import { OperatorToken } from "./OperatorToken";
import { MailCard } from "./MailCard";
import { SessionList } from "./SessionList";
import { mailKind, type MailFilter } from "./queue";
import "./review.css";

const POLL_MS = 5000;
const statusOf = (message: string) => Number(message.match(/^(\d{3})\b/)?.[1] ?? 0);
const messageOf = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

/** Polls /api/review every 5 s while the tab is visible; stops on 404 (endpoint not in this build). */
function useReviewQueue() {
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const missingRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await getReview();
      setQueue(next);
      setError(null);
      setMissing(false);
      missingRef.current = false;
      setUpdatedAt(Date.now());
    } catch (cause) {
      const message = messageOf(cause);
      if (statusOf(message) === 404) {
        setMissing(true);
        missingRef.current = true;
      } else {
        setError(message);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !missingRef.current) void refresh();
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [refresh]);

  return { queue, error, loading, missing, updatedAt, refresh };
}

export function ReviewPage() {
  const { queue, error, loading, missing, updatedAt, refresh } = useReviewQueue();
  const [filter, setFilter] = useState<MailFilter>("all");
  const [needsToken, setNeedsToken] = useState(false);
  const sessionsRef = useRef<HTMLElement>(null);
  const mailRef = useRef<HTMLElement>(null);

  const mail = queue?.mail ?? [];
  const counts = {
    sessions: queue?.sessions_total ?? 0,
    unmatched: mail.filter((m) => mailKind(m) === "unmatched").length,
    tariff: mail.filter((m) => mailKind(m) === "tariff").length,
    bid: mail.filter((m) => mailKind(m) === "bid").length,
  };
  const mailCut = queue ? queue.mail_total > queue.mail.length : false;
  const shownMail = filter === "all" ? mail : mail.filter((m) => mailKind(m) === filter);
  const nothing = queue !== null && queue.sessions_total === 0 && queue.mail_total === 0;

  /** Resolves one mail. Returns an error message for the card, or null on success. */
  const resolve = async (externalId: string, approve: boolean): Promise<string | null> => {
    try {
      await resolveMail(externalId, approve);
      setNeedsToken(false);
      await refresh();
      return null;
    } catch (cause) {
      const message = messageOf(cause);
      const status = statusOf(message);
      if (status === 401) {
        setNeedsToken(true);
        return "Operator token missing or wrong. Enter it above, then try again.";
      }
      if (status === 404) return "This mail is no longer in the queue.";
      return message;
    }
  };

  const jump = (target: "sessions" | MailFilter) => {
    if (target === "sessions") {
      sessionsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }
    setFilter(target);
    mailRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="page rv">
      <header className="page-head rv-head">
        <div>
          <h1 className="display">Review queue</h1>
          <p>Everything the agents could not finish alone and that now waits for a person.</p>
        </div>
        <div className="rv-head-side">
          {!missing && (
            <span className="rv-live subtle" aria-live="off">
              <span className={`dot ${error ? "" : "dot-live"} rv-live-dot`} aria-hidden="true" />
              {error ? "Refresh failed, retrying" : updatedAt ? "Live, every 5 s" : "Connecting"}
            </span>
          )}
          <OperatorToken needed={needsToken} />
        </div>
      </header>

      {missing ? (
        <NotExposed onRetry={() => void refresh()} />
      ) : (
        <>
          {error && !queue && (
            <div className="error-box" role="alert">
              Could not load the review queue. {error}
            </div>
          )}
          {error && queue && (
            <p className="rv-stale" role="status">
              Showing the last good data. The latest refresh failed: {error}
            </p>
          )}

          <section className="rv-tiles" aria-label="Summary">
            <Tile
              label="Blocked sessions"
              value={queue ? counts.sessions : null}
              hint="Paused, expired or stuck"
              onClick={() => jump("sessions")}
            />
            <Tile
              label="Unmatched mail"
              value={queue ? counts.unmatched : null}
              hint="No shipment found"
              onClick={() => jump("unmatched")}
              cut={mailCut}
            />
            <Tile
              label="Tariff files"
              value={queue ? counts.tariff : null}
              hint="Need approval"
              onClick={() => jump("tariff")}
              cut={mailCut}
            />
            <Tile
              label="Carrier bids"
              value={queue ? counts.bid : null}
              hint="Need approval"
              onClick={() => jump("bid")}
              cut={mailCut}
            />
          </section>

          {loading && !queue && <LoadingRows />}

          {nothing && (
            <div className="rv-calm">
              <span className="rv-calm-mark" aria-hidden="true">
                ✓
              </span>
              <h2 className="rv-calm-title">Nothing waiting for a person.</h2>
              <p className="muted">The desk is running on its own.</p>
            </div>
          )}

          {queue && queue.sessions_total > 0 && (
            <section ref={sessionsRef} className="rv-section" aria-labelledby="rv-sessions-h">
              <div className="rv-section-head">
                <h2 id="rv-sessions-h" className="rv-section-title">
                  Blocked sessions
                </h2>
                <span className="subtle num">
                  {queue.sessions.length < queue.sessions_total
                    ? `Newest ${queue.sessions.length} of ${queue.sessions_total}`
                    : `${queue.sessions_total} total`}
                </span>
              </div>
              <SessionList sessions={queue.sessions} />
            </section>
          )}

          {queue && queue.mail_total > 0 && (
            <section ref={mailRef} className="rv-section" aria-labelledby="rv-mail-h">
              <div className="rv-section-head">
                <h2 id="rv-mail-h" className="rv-section-title">
                  Mail
                </h2>
                <span className="subtle num">
                  {mailCut ? `Newest ${mail.length} of ${queue.mail_total}` : `${queue.mail_total} total`}
                </span>
              </div>
              <div className="rv-filters" role="group" aria-label="Filter mail">
                {(
                  [
                    ["all", "All", mail.length],
                    ["unmatched", "Unmatched", counts.unmatched],
                    ["tariff", "Tariff files", counts.tariff],
                    ["bid", "Carrier bids", counts.bid],
                  ] as const
                ).map(([key, label, count]) => (
                  <button
                    key={key}
                    type="button"
                    className={`rv-filter ${filter === key ? "is-on" : ""}`}
                    aria-pressed={filter === key}
                    onClick={() => setFilter(key)}
                  >
                    {label} <span className="num rv-filter-count">{count}</span>
                  </button>
                ))}
              </div>
              {shownMail.length === 0 ? (
                <p className="empty">No mail of this kind is waiting.</p>
              ) : (
                <ul className="rv-mail-list">
                  {shownMail.map((item) => (
                    <li key={item.external_id}>
                      <MailCard mail={item} onResolve={resolve} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </>
      )}
    </div>
  );
}

function Tile({
  label,
  value,
  hint,
  onClick,
  cut = false,
}: {
  label: string;
  value: number | null;
  hint: string;
  onClick: () => void;
  cut?: boolean;
}) {
  const active = value !== null && value > 0;
  return (
    <button
      type="button"
      className={`rv-tile ${active ? "is-active" : ""}`}
      onClick={onClick}
      disabled={!active}
    >
      <span className="rv-tile-label">{label}</span>
      {value === null ? (
        <span className="skeleton rv-tile-skel" aria-hidden="true" />
      ) : (
        <span className="rv-tile-value num">{value}</span>
      )}
      <span className="rv-tile-hint">
        {hint}
        {cut ? " · in newest shown" : ""}
      </span>
    </button>
  );
}

function LoadingRows() {
  return (
    <div className="rv-loading" aria-busy="true" aria-label="Loading the review queue">
      {[0, 1, 2].map((i) => (
        <div key={i} className="skeleton rv-loading-row" />
      ))}
    </div>
  );
}

function NotExposed({ onRetry }: { onRetry: () => void }) {
  return (
    <section className="rv-off card card-tint" aria-labelledby="rv-off-h">
      <span className="chip">
        <span className="dot" aria-hidden="true" /> 404 from /api/review
      </span>
      <h2 id="rv-off-h" className="rv-off-title">
        Not exposed by this backend build yet.
      </h2>
      <p className="muted">
        The API you are connected to is an older build without the review endpoint. Nothing is
        broken: sessions with an exception are still listed on the Sessions page. Use Check again
        once a newer backend serves <span className="mono">GET /api/review</span>.
      </p>
      <div className="rv-off-actions">
        <Link className="btn btn-primary" to="/sessions">
          Open sessions
        </Link>
        <button className="btn" type="button" onClick={onRetry}>
          Check again
        </button>
      </div>
    </section>
  );
}
