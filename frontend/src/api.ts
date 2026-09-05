// Thin typed client for the RevGuard API. It only performs HTTP calls and JSON (de)serialization;
// all business logic lives in the backend services. No secrets are ever read or sent here.

import type {
  AuditTrail,
  BatchMetrics,
  CaseDetail,
  CaseList,
  EvaluationReport,
  EventAccepted,
  EventIn,
  RecoveryAnalytics,
  RunResult,
  StatusInfo,
} from "./types";

const BASE_URL: string =
  (import.meta.env?.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ??
  "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${BASE_URL}. Is the backend running?`);
  }
  if (!resp.ok) {
    throw new ApiError(resp.status, await extractError(resp));
  }
  return (await resp.json()) as T;
}

async function extractError(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return "Validation error: check the submitted fields.";
  } catch {
    /* fall through to status text */
  }
  return resp.statusText || `Request failed (${resp.status})`;
}

export const api = {
  baseUrl: BASE_URL,

  health: () => request<{ status: string }>("/health"),

  status: () => request<StatusInfo>("/status"),

  listCases: () => request<CaseList>("/cases"),

  getCase: (caseId: string) => request<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`),

  getAudit: (caseId: string) =>
    request<AuditTrail>(`/cases/${encodeURIComponent(caseId)}/audit`),

  runCase: (caseId: string) =>
    request<RunResult>(`/cases/${encodeURIComponent(caseId)}/run`, { method: "POST" }),

  // Human-in-the-loop: an operator authorizes one bounded action on an escalated case.
  authorizeCase: (caseId: string, action: string, operator = "operator") =>
    request<RunResult>(`/cases/${encodeURIComponent(caseId)}/authorize`, {
      method: "POST",
      body: JSON.stringify({ action, operator }),
    }),

  // DEMO ONLY deterministic test doubles for a WAITING case. Neither exists in production:
  // there, recovery is confirmed solely by Razorpay's verified paid status (webhook).
  // simulatePayment drives the case to RECOVERED through the real verified-confirmation path;
  // markUnpaid records a verified NOT_RECOVERED (₹0) and lets the PolicyEngine's bounded retry
  // logic decide the next attempt or a terminal STOP/ESCALATE.
  simulatePayment: (caseId: string) =>
    request<RunResult>(`/cases/${encodeURIComponent(caseId)}/simulate_payment`, {
      method: "POST",
    }),

  markUnpaid: (caseId: string) =>
    request<RunResult>(`/cases/${encodeURIComponent(caseId)}/mark_unpaid`, {
      method: "POST",
    }),

  // Provider-backed status re-poll (production). Asks the configured verifier for Razorpay's
  // current verified status: paid → RECOVERED with the verified amount, otherwise stays WAITING.
  // Never fabricates recovery; complements the *.paid webhook.
  recheckPayment: (caseId: string) =>
    request<RunResult>(`/cases/${encodeURIComponent(caseId)}/recheck_payment`, {
      method: "POST",
    }),

  // Advance a promise-to-pay: on/after the promised time this verifies the actual payment
  // state and either recovers the case or routes a missed promise to escalation/stop.
  checkPromise: (caseId: string) =>
    request<RunResult>(`/cases/${encodeURIComponent(caseId)}/check_promise`, {
      method: "POST",
    }),

  postEvent: (event: EventIn) =>
    request<EventAccepted>("/events", { method: "POST", body: JSON.stringify(event) }),

  // Clear all cases, events, and audit history so the model can be run from scratch.
  reset: () =>
    request<{ cases: number; events: number; audit: number }>("/reset", { method: "POST" }),

  metrics: () => request<BatchMetrics>("/metrics"),

  analytics: () => request<RecoveryAnalytics>("/analytics"),

  evaluation: (seed?: number) =>
    request<EvaluationReport>(`/evaluation${seed != null ? `?seed=${seed}` : ""}`),
};
