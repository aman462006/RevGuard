// Presentation helpers. Money/rate values arrive as decimal strings from the API; we format
// for display only (never for computation) so we never introduce float error into the numbers.

import type { CaseStatus, DataProvenance, WorkflowType } from "./types";

export function money(value: string | number, currency = "INR"): string {
  const n = typeof value === "number" ? value : Number.parseFloat(value);
  if (Number.isNaN(n)) return String(value);
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(n);
  } catch {
    return `${currency} ${n.toFixed(2)}`;
  }
}

// A rate is a fraction in [0, 1] (decimal string like "0.7500"). Render as a percentage.
export function percent(rate: string | number, digits = 1): string {
  const n = typeof rate === "number" ? rate : Number.parseFloat(rate);
  if (Number.isNaN(n)) return String(rate);
  return `${(n * 100).toFixed(digits)}%`;
}

export function signedMoney(value: string | number, currency = "INR"): string {
  const n = typeof value === "number" ? value : Number.parseFloat(value);
  const sign = n >= 0 ? "+" : "-";
  return `${sign}${money(Math.abs(n), currency)}`;
}

export function signedInt(value: number): string {
  return `${value >= 0 ? "+" : ""}${value}`;
}

export function dateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

const WORKFLOW_LABELS: Record<WorkflowType, string> = {
  payment_degradation: "Payment degradation",
  failed_subscription: "Failed subscription",
  checkout_abandonment: "Checkout abandonment",
  overdue_receivable: "Overdue receivable",
};

export function workflowLabel(w: WorkflowType | string): string {
  return WORKFLOW_LABELS[w as WorkflowType] ?? titleCase(w);
}

const PROVENANCE_LABELS: Record<DataProvenance, string> = {
  synthetic: "Synthetic",
  razorpay: "Razorpay",
  internal: "Internal",
};

// Short label for a case's data origin. Synthetic reads as "Demo" to make the demo nature of
// the data unmistakable at a glance.
export function provenanceLabel(p: DataProvenance | string): string {
  if (p === "synthetic") return "Demo";
  return PROVENANCE_LABELS[p as DataProvenance] ?? titleCase(p);
}

export function titleCase(value: string): string {
  return value
    .split("_")
    .map((p) => (p ? p[0].toUpperCase() + p.slice(1) : p))
    .join(" ");
}

// Statuses grouped for the Overview tiles.
export const ACTIVE_STATUSES: CaseStatus[] = [
  "detected",
  "analyzing",
  "action_pending",
  "action_approved",
  "action_executing",
  "waiting",
];

export function isActive(status: CaseStatus): boolean {
  return ACTIVE_STATUSES.includes(status);
}
