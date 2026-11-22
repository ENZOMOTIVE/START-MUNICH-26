import { useEffect, useRef, useState } from "react";
import type { SessionSummary } from "../../types/session";

export type FeedEvent = {
  key: string;
  at: number;
  kind: "new" | "stage" | "attention";
  id: string;
  lane: string;
  text: string;
};

const KEEP = 14;
const lane = (s: SessionSummary) => {
  const o = s.origin ?? s.rfq?.origin;
  const d = s.destination ?? s.rfq?.destination;
  return o && d ? `${o} → ${d}` : "RFQ";
};

/**
 * Compares each polled session list with the previous one and reports what really changed:
 * a session that appeared, moved to another stage, or needs a person. The first list only
 * sets the baseline, so nothing is invented when the page opens. Nothing here is simulated.
 */
export function useLiveFeed(list: SessionSummary[] | null) {
  const previous = useRef<Map<string, SessionSummary> | null>(null);
  const counter = useRef(0);
  const [events, setEvents] = useState<FeedEvent[]>([]);
  const [total, setTotal] = useState(0);

  useEffect(() => {
    if (!list) return;
    const now = new Map(list.map((s) => [s.id, s]));
    const before = previous.current;
    previous.current = now;
    if (!before) return;
    const found: FeedEvent[] = [];
    for (const s of list) {
      const old = before.get(s.id);
      const base = { at: Date.now(), id: s.id, lane: lane(s), key: `${s.id}-${counter.current++}` };
      if (!old) found.push({ ...base, kind: "new", text: "New RFQ received" });
      else if (old.stage !== s.stage)
        found.push({ ...base, kind: "stage", text: `${old.stage} → ${s.stage}` });
      else if (!old.exception && s.exception)
        found.push({ ...base, kind: "attention", text: `Needs attention: ${s.exception}` });
    }
    if (found.length) {
      setEvents((e) => [...found.reverse(), ...e].slice(0, KEEP));
      setTotal((t) => t + found.length);
    }
  }, [list]);

  return { events, total };
}
