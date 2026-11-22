import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Loads `fn` now and then every `intervalMs` while the tab is visible.
 * Keeps the last good data on a failed refresh and reports the error separately.
 * `key` restarts polling when it changes (e.g. a route id).
 */
export function usePolling<T>(fn: () => Promise<T>, intervalMs = 5000, key = "") {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const refresh = useCallback(async () => {
    try {
      const next = await fnRef.current();
      setData(next);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void refresh();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, refresh, key]);

  return { data, error, loading, refresh };
}

/** Re-renders every `ms` so countdowns move. */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(t);
  }, [ms]);
  return now;
}
