import type {
  DemoRunResult,
  Meta,
  SessionDetail,
  SessionEvent,
  SessionSummary,
} from "../types/session";

const API_URL = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      // Entered on the review page; kept in this browser only.
      ...(localStorage.getItem("operator_token")
        ? { "X-Operator-Token": localStorage.getItem("operator_token") as string }
        : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}${body ? `: ${body}` : ""}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export function getMeta(): Promise<Meta> {
  return request("/api/meta");
}

export function listSessions(): Promise<SessionSummary[]> {
  return request("/api/sessions");
}

export function getSession(id: string): Promise<SessionDetail> {
  return request(`/api/sessions/${id}`);
}

export function getEvents(id: string): Promise<SessionEvent[]> {
  return request(`/api/sessions/${id}/events`);
}

export function pauseSession(id: string): Promise<SessionDetail> {
  return request(`/api/sessions/${id}/pause`, { method: "POST" });
}

export function resumeSession(id: string): Promise<SessionDetail> {
  return request(`/api/sessions/${id}/resume`, { method: "POST" });
}

export type SessionAction = {
  id: string;
  kind: string;
  purpose: string | null;
  delivery_status: string;
  to: string | null;
  subject: string | null;
  body: string | null;
  edited_by_operator: boolean;
  simulated: boolean;
};

export function listActions(id: string): Promise<SessionAction[]> {
  return request(`/api/sessions/${id}/actions`);
}

export function editAction(id: string, actionId: string, body: string): Promise<SessionAction> {
  return request(`/api/sessions/${id}/actions/${actionId}`, {
    method: "PUT",
    body: JSON.stringify({ body }),
  });
}

export function sendAction(id: string, actionId: string): Promise<SessionAction> {
  return request(`/api/sessions/${id}/actions/${actionId}/send`, { method: "POST" });
}

export function resolveAction(id: string, actionId: string, sent: boolean): Promise<SessionAction> {
  return request(`/api/sessions/${id}/actions/${actionId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ sent }),
  });
}

export type ReviewQueue = {
  sessions_total: number;
  mail_total: number;
  sessions: { id: string; stage: string; reason: string; paused: boolean }[];
  mail: {
    external_id: string;
    sender: string;
    subject: string;
    kind: string | null;
    unmatched: { kind: string; reason: string } | null;
    tariff: { status: string; files: { filename: string; status: string }[] } | null;
    bid: {
      status: string;
      reasons: string[];
      carrier: string;
      amount: string | null;
      currency: string | null;
    } | null;
  }[];
};

export function getReview(): Promise<ReviewQueue> {
  return request("/api/review");
}

export function resolveMail(externalId: string, approve: boolean): Promise<unknown> {
  return request(`/api/review/mail/${externalId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ approve }),
  });
}

export type SandboxScenario = { id: string; title: string; version: number };
export type SandboxMail = {
  direction: "in" | "out";
  at_s: number;
  subject: string;
  body: string;
  purpose?: string;
  attachments?: string[];
};
export type SandboxResult = {
  scenario: string;
  title: string;
  seed: number;
  passed: boolean;
  digest: string;
  simulated_seconds: number;
  wall_seconds: number;
  data: string;
  checks: { check: string; expected: unknown; actual: unknown; passed: boolean }[];
  requests: Record<string, Record<string, unknown>>;
  totals: Record<string, number>;
  threads: Record<string, SandboxMail[]>;
  faults: { at_s: number; where: string; fault: string }[];
  errors: Record<string, unknown>[];
  rejection_reasons: string[];
};

// These routes exist only when the API runs with APP_MODE=sandbox.
export function listSandboxScenarios(): Promise<SandboxScenario[]> {
  return request("/sandbox/scenarios");
}

export function runSandboxScenario(
  scenario: string,
  seed: number,
  untilS: number | null,
): Promise<SandboxResult> {
  const body = JSON.stringify({ scenario, seed, until_s: untilS });
  return request("/sandbox/run", { method: "POST", body });
}

export async function runDemo(): Promise<{ id: string }> {
  const data = await request<DemoRunResult | string>("/internal/demo/run", {
    method: "POST",
  });
  if (typeof data === "string") {
    return { id: data };
  }
  const id = data.id ?? data.session_id;
  if (!id) {
    throw new Error("demo run did not return a session id");
  }
  return { id };
}

// ---- dashboard + live data (existing backend endpoints, unchanged) ----

export type UsageRow = {
  model: string;
  cost_kind: string;
  simulated: boolean;
  calls: number;
  observed_cost: string | null;
  estimated_cost: string | null;
};

/** Model gateway calls grouped by model and cost kind. */
export function getUsage(): Promise<UsageRow[]> {
  return request("/api/usage");
}

export type RunStatus = {
  enabled: boolean;
  started_ms: number | null;
  stopped_ms: number | null;
  minutes: number;
  admitted_threads: string[];
  used: Record<string, number>;
  limits: Record<string, number>;
};

/** Counters and limits of the bounded live run. */
export function getRunStatus(): Promise<RunStatus> {
  return request("/api/run");
}

export function stopRun(): Promise<RunStatus> {
  return request("/api/run/stop", { method: "POST" });
}

export function admitSession(id: string): Promise<SessionDetail> {
  return request(`/api/sessions/${id}/admit`, { method: "POST" });
}

/**
 * Server-sent events for one session's timeline. Calls `onEvent` once per event, in order.
 * The server names each SSE message after the event type, so this reads the stream itself
 * instead of EventSource (which would need one listener per type). On a drop it reconnects
 * with `after=<last id>`, so no event is delivered twice. Returns a function that stops it.
 */
export function streamSessionEvents(
  id: string,
  onEvent: (event: SessionEvent) => void,
  onStatus?: (status: "open" | "reconnecting" | "unavailable") => void,
): () => void {
  const controller = new AbortController();
  let lastId = 0;

  const run = async () => {
    while (!controller.signal.aborted) {
      try {
        const res = await fetch(`${API_URL}/api/sessions/${id}/stream?after=${lastId}`, {
          headers: { Accept: "text/event-stream" },
          signal: controller.signal,
        });
        if (res.status >= 400 && res.status < 500) {
          // 404: this backend build has no stream (or no such session). Retrying won't help.
          onStatus?.("unavailable");
          return;
        }
        if (!res.ok || !res.body) {
          throw new Error(`stream ${res.status}`);
        }
        onStatus?.("open");
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buffer = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += value;
          let cut = buffer.indexOf("\n\n");
          while (cut >= 0) {
            const block = buffer.slice(0, cut);
            buffer = buffer.slice(cut + 2);
            cut = buffer.indexOf("\n\n");
            let eventId: number | null = null;
            const data: string[] = [];
            for (const line of block.split("\n")) {
              if (line.startsWith("id:")) eventId = Number(line.slice(3).trim());
              else if (line.startsWith("data:")) data.push(line.slice(5).trim());
            }
            if (!data.length) continue; // keep-alive comment
            if (eventId !== null && Number.isFinite(eventId)) lastId = eventId;
            try {
              onEvent(JSON.parse(data.join("\n")) as SessionEvent);
            } catch {
              // a malformed message is skipped, the stream goes on
            }
          }
        }
      } catch {
        if (controller.signal.aborted) return;
      }
      onStatus?.("reconnecting");
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  };

  void run();
  return () => controller.abort();
}
