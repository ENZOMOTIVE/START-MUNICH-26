/**
 * Interim types matching backend JSON.
 * `pnpm openapi` replaces these after exporting backend OpenAPI.
 */

export type SessionStage =
  | "intake"
  | "pricing"
  | "quoted"
  | "accepted"
  | "booked"
  | "exception";

export type Meta = {
  app_mode: "demo" | "live" | "sandbox";
  live_actions_enabled: boolean;
  demo?: boolean;
  sandbox?: boolean;
};

export type RfqPayload = {
  origin: string;
  destination: string;
  weight?: string;
  weight_kg?: string;
  volume_m3?: string | null;
  pickup_required?: boolean;
  pickup_zone?: string | null;
  service_intent?: "cheapest" | "fastest" | string | null;
  received_at?: string;
  expires_at?: string;
  thread_id?: string;
  status?: string;
};

export type QuotePayload = {
  total: string;
  currency: string;
  carrier?: string;
  decided_at?: string;
  evidence?: Record<string, unknown>;
};

export type BookingPayload = {
  status: string;
  reference?: string | null;
  booking_reference?: string | null;
};

export type SessionEvent = {
  id: string;
  session_id?: string;
  type: string;
  payload?: Record<string, unknown>;
  occurred_at: string;
  simulated: boolean;
};

export type SelectedOffer = {
  carrier?: string;
  total?: string;
  currency?: string;
  transit_days?: number | null;
  source_ref?: string;
  source_version?: string;
  simulated?: boolean;
  evidence?: Record<string, unknown>;
};

export type SessionSummary = {
  id: string;
  origin?: string | null;
  destination?: string | null;
  stage: SessionStage | string;
  deadline: string;
  paused: boolean;
  exception: string | null;
  simulated?: boolean;
  carrier?: string | null;
  quote_total?: string | null;
  quote_currency?: string | null;
  rfq?: RfqPayload;
};

export type SessionDetail = SessionSummary & {
  request?: RfqPayload;
  selected_offer?: SelectedOffer | null;
  evidence?: Record<string, unknown> | null;
  quote?: QuotePayload | null;
  booking?: BookingPayload | null;
  events?: SessionEvent[];
};

export type DemoRunResult = {
  id?: string;
  session_id?: string;
};
