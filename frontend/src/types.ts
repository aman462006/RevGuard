// Types mirroring the RevGuard API contract (src/revguard/api/schemas.py + metrics models).
// Money and rates are serialized as decimal strings by the backend; keep them as strings and
// format at the edges so we never lose precision to float.

export type CaseStatus =
  | "detected"
  | "analyzing"
  | "action_pending"
  | "action_approved"
  | "action_executing"
  | "waiting"
  | "recovered"
  | "escalated"
  | "stopped"
  | "failed";

export type WorkflowType =
  | "payment_degradation"
  | "failed_subscription"
  | "checkout_abandonment"
  | "overdue_receivable";

export type RiskLevel = "low" | "medium" | "high" | "critical";

// Data origin of a case/signal. "synthetic" is generated demo data (never real money);
// "razorpay"/"internal" are real (test-mode or live) provider/integration data.
export type DataProvenance = "synthetic" | "razorpay" | "internal";

export interface CaseSummary {
  case_id: string;
  case_type: WorkflowType;
  status: CaseStatus;
  is_terminal: boolean;
  customer_id: string | null;
  risk_level: RiskLevel;
  currency: string;
  amount_at_risk: string;
  amount_recovered: string;
  attempt_count: number;
  updated_at: string;
  // Data origin — is_synthetic is true ONLY for generated demo data, never for live data.
  provenance: DataProvenance;
  is_synthetic: boolean;
}

// One provenance dimension in the case detail (case, transaction, action, or verification).
export interface ProvenanceFacet {
  label: string;
  synthetic: boolean;
  detail: string;
}

// Provenance of the four things a case is built from, so demo/test data is never mistaken for
// live production customer data.
export interface CaseProvenance {
  case: ProvenanceFacet;
  transaction: ProvenanceFacet;
  recovery_action: ProvenanceFacet;
  payment_verification: ProvenanceFacet;
}

// One entry in a case's deterministic retry history (reconstructed from the audit log).
export interface RetryAttempt {
  attempt: number;
  action: string | null;
  executed_at: string;
  outcome: string; // "executed" | "recovered" | "pending" | "not_recovered" | "failed"
  next_retry_at: string | null;
}

// A case's promise-to-pay lifecycle (only present when a promise was recorded).
export interface Promise {
  status: "promised" | "pending" | "kept" | "missed";
  promised_at: string;
  recorded_at: string;
  amount: string | null;
  reference: string | null;
  verification_reference: string | null;
  resolved_at: string | null;
}

export interface CaseDetail extends CaseSummary {
  current_action: string | null;
  current_step: number;
  created_at: string;
  escalated_at: string | null;
  escalation_reason: string | null;
  stopped_at: string | null;
  stop_reason: string | null;
  // Promise-to-pay lifecycle, when the case recorded a promise.
  promise?: Promise | null;
  // Deterministic retry sequencing: next eligible retry time, the bounded attempt budget, and
  // the per-attempt history. next_retry_at is null unless a retry is currently scheduled.
  next_retry_at?: string | null;
  max_attempts?: number;
  retry_history?: RetryAttempt[];
  // Detector evidence for why the case was raised (e.g. the payment-degradation root-cause
  // breakdown). Shape varies by workflow; read defensively.
  signal_evidence?: Record<string, unknown>;
  // Provenance of the case, its transaction data, the recovery action, and the verification.
  provenance_detail: CaseProvenance;
  // Consent / do-not-contact state. When do_not_contact is true, blocked_contact_actions lists
  // the customer-contact actions the backend will refuse to execute.
  do_not_contact: boolean;
  blocked_contact_actions: string[];
}

export interface CaseList {
  cases: CaseSummary[];
  count: number;
}

export interface RunResult {
  case: CaseDetail;
  recovered: boolean;
  amount_recovered: string;
}

export interface EventAccepted {
  event_id: string;
  accepted: boolean;
  cases: CaseSummary[];
}

export interface AuditEntry {
  seq: number | null;
  stage: string;
  actor: string;
  action: string | null;
  recorded_at: string;
  correlation_id: string | null;
  details: Record<string, unknown>;
}

export interface AuditTrail {
  case_id: string;
  entries: AuditEntry[];
}

export interface WorkflowMetrics {
  workflow: WorkflowType;
  total_cases: number;
  revenue_at_risk: string;
  recovered_amount: string;
  unresolved_amount: string;
  recovery_rate: string;
  recovered: number;
  escalated: number;
  stopped: number;
  failed: number;
  open_cases: number;
}

export interface BatchMetrics {
  strategy: string;
  total_cases: number;
  revenue_at_risk: string;
  recovered_amount: string;
  unresolved_amount: string;
  recovery_rate: string;
  recovered: number;
  escalated: number;
  stopped: number;
  failed: number;
  open_cases: number;
  unresolved_cases: number;
  by_workflow: Partial<Record<WorkflowType, WorkflowMetrics>>;
}

export interface StrategyDelta {
  revenue_recovered_delta: string;
  recovery_rate_delta: string;
  recovered_cases_delta: number;
  escalated_delta: number;
  stopped_delta: number;
  failed_delta: number;
}

export interface Comparison {
  baseline: BatchMetrics;
  revguard: BatchMetrics;
  delta: StrategyDelta;
}

export interface EvaluationReport {
  seed: number;
  comparison: Comparison;
}

// -- Recovery analytics (deterministic, from persisted cases + audit log) --------------

// Effectiveness of one intervention/action type at actually recovering money.
export interface ActionEffectiveness {
  action: string;
  attempts: number;
  recoveries: number;
  recovered_amount: string;
  recovery_rate: string; // fraction in [0,1]
}

// The main case outcomes.
export interface OutcomeBreakdown {
  recovered: number;
  escalated: number;
  stopped: number;
  failed: number;
  pending: number;
  recovered_amount: string;
  at_risk_amount: string;
  pending_amount: string;
}

// Recovery activity for one calendar day (UTC).
export interface ActivityBucket {
  date: string; // YYYY-MM-DD
  recovered_amount: string;
  recovered_count: number;
  attempts: number;
}

export interface RecoveryAnalytics {
  total_cases: number;
  total_attempts: number;
  total_recovered_amount: string;
  overall_recovery_rate: string;
  by_action: ActionEffectiveness[];
  outcomes: OutcomeBreakdown;
  timeline: ActivityBucket[];
}

// Runtime mode + provider readiness (GET /status) — never contains secrets.
export interface StatusInfo {
  mode: string; // "demo" | "production"
  demo: boolean;
  ai_provider: string;
  ai_configured: boolean;
  razorpay_configured: boolean;
  razorpay_test_mode: boolean;
  webhook_configured: boolean;
  run_ready: boolean;
}

// Shape of an event submitted to POST /events (a subset of EventIn).
export interface EventIn {
  event_type: string;
  source?: string;
  customer_id?: string;
  subscription_id?: string;
  invoice_id?: string;
  order_id?: string;
  payment_id?: string;
  amount?: string;
  currency?: string;
  metadata?: Record<string, unknown>;
}
