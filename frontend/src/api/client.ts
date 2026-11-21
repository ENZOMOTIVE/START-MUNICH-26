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
