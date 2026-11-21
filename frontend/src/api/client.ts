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
      // Set once in the browser console: localStorage.setItem("operator_token", "<token>")
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
