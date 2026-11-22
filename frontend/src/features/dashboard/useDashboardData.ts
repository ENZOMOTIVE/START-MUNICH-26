import { useEffect, useRef, useState } from "react";
import { getMeta, getRunStatus, getSession, getUsage, listSessions } from "../../api/client";
import type { RunStatus, UsageRow } from "../../api/client";
import { usePolling } from "../../lib/usePolling";
import type { SessionSummary } from "../../types/session";
import type { LooseDetail } from "./metrics";

/** How many of the most recent sessions get their detail loaded. */
export const DETAIL_WINDOW = 40;
const MAX_IN_FLIGHT = 6;
const REFRESH_OPEN_MS = 15_000;

/** An optional endpoint: either its data, or "this backend build does not expose it". */
export type Optional<T> = { available: true; data: T } | { available: false };

function optional<T>(fn: () => Promise<T>): () => Promise<Optional<T>> {
  return async () => {
    try {
      return { available: true, data: await fn() };
    } catch (err) {
      if (err instanceof Error && err.message.startsWith("404")) return { available: false };
      throw err;
    }
  };
}

const fetchUsage = optional<UsageRow[]>(getUsage);
const fetchRun = optional<RunStatus>(getRunStatus);

/** Newest first by deadline (the list has no received timestamp; deadline = received + 6 min). */
export function newestFirst(list: SessionSummary[]): SessionSummary[] {
  return [...list].sort((a, b) => Date.parse(b.deadline) - Date.parse(a.deadline));
}

type CacheEntry = { detail: LooseDetail; fetchedAt: number };

/**
 * Detail cache for the newest DETAIL_WINDOW sessions. Each list refresh fetches only
 * sessions not seen yet, sessions whose stage moved, and (at most every 15 s) sessions
 * that are not booked yet. At most six requests are in flight.
 */
function useDetails(list: SessionSummary[] | null) {
  const cache = useRef(new Map<string, CacheEntry>());
  const inFlight = useRef(new Set<string>());
  const [version, setVersion] = useState(0);
  const [failed, setFailed] = useState(0);
  const [settled, setSettled] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!list) return;
    const now = Date.now();
    const wanted = newestFirst(list).slice(0, DETAIL_WINDOW);
    const todo = wanted
      .filter((s) => {
        if (inFlight.current.has(s.id)) return false;
        const hit = cache.current.get(s.id);
        if (!hit) return true;
        if (hit.detail.stage !== s.stage || hit.detail.exception !== s.exception) return true;
        return hit.detail.stage !== "booked" && now - hit.fetchedAt > REFRESH_OPEN_MS;
      })
      .map((s) => s.id);
    if (todo.length === 0) {
      setSettled(true);
      return;
    }

    let errors = 0;
    const queue = [...todo];
    todo.forEach((id) => inFlight.current.add(id));
    const worker = async () => {
      for (let id = queue.shift(); id !== undefined; id = queue.shift()) {
        try {
          const detail = (await getSession(id)) as LooseDetail;
          cache.current.set(id, { detail, fetchedAt: Date.now() });
        } catch {
          errors += 1;
        } finally {
          inFlight.current.delete(id);
        }
      }
    };
    void Promise.all(Array.from({ length: Math.min(MAX_IN_FLIGHT, todo.length) }, worker)).then(() => {
      // A newer list poll may have started meanwhile; the cache is still valid, so render it.
      if (!mounted.current) return;
      setFailed(errors);
      setSettled(true);
      setVersion((v) => v + 1);
    });
  }, [list]);

  // Recompute the visible set only when the cache or the list changes.
  const ids = list ? newestFirst(list).slice(0, DETAIL_WINDOW).map((s) => s.id) : [];
  const details = ids
    .map((id) => cache.current.get(id)?.detail)
    .filter((d): d is LooseDetail => d !== undefined);

  return { details, failed, settled, version };
}

export function useDashboardData() {
  const sessions = usePolling(listSessions, 2000);
  const meta = usePolling(getMeta, 30_000);
  const usage = usePolling(fetchUsage, 30_000);
  const run = usePolling(fetchRun, 10_000);
  const details = useDetails(sessions.data);
  return { sessions, meta, usage, run, details };
}
