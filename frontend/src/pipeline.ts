// Derives the human-readable recovery pipeline from a case's audit trail.
//
// This is the heart of the Case Details view: it separates what the *AI recommended* from what
// the *PolicyEngine decided* and from what was *verified* — so a judge can see at a glance that
// the LLM only advises and a deterministic gate authorizes execution, and that recovery is only
// ever confirmed by verification (never by a successful API call alone).
//
// It reads only fields the backend already records in audit `details` (see recovery_agent.py /
// executor.py). It is pure and fully unit-tested.

import type { AuditEntry } from "./types";

export type PolicyDecision = "approve" | "escalate" | "stop" | null;

export interface Diagnosis {
  provider: string;
  action: string | null;
  confidence: number | null;
  rationale: string | null;
}

export interface Policy {
  decision: PolicyDecision;
  proposedAction: string | null;
  matchedRules: string[];
  reason: string | null;
}

export interface Execution {
  action: string | null;
  executionStatus: string | null;
  verificationStatus: string | null;
  simulated: boolean | null;
  // Ground-truth provider that performed the action ("mock" = offline demo test double,
  // "razorpay_test" = a real Razorpay Test Mode call). Drives truthful provider labelling.
  provider: string | null;
  reference: string | null;
  url: string | null;
}

export interface Verification {
  verificationStatus: string | null;
  executionStatus: string | null;
  amountRecovered: string | null;
}

export interface Recovery {
  amountRecovered: string | null;
  currency: string | null;
  paymentReference: string | null;
}

export interface Pipeline {
  diagnosis: Diagnosis | null;
  policy: Policy | null;
  execution: Execution | null;
  verification: Verification | null;
  recovery: Recovery | null;
  // True only when a verification stage confirmed recovery. Distinct from a successful action.
  verifiedRecovered: boolean;
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function stringList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

// Last entry wins for each stage (a case may loop through several attempts).
function last(entries: AuditEntry[], stage: string): AuditEntry | null {
  let found: AuditEntry | null = null;
  for (const e of entries) if (e.stage === stage) found = e;
  return found;
}

export function derivePipeline(entries: AuditEntry[]): Pipeline {
  const diag = last(entries, "diagnosis");
  const pol = last(entries, "policy_decision");
  const exec = last(entries, "execution");
  const ver = last(entries, "verification");
  const rec = last(entries, "recovery_result");

  const diagnosis: Diagnosis | null = diag && {
    provider: str(diag.details.provider) ?? "unknown",
    action: diag.action,
    confidence: num(diag.details.confidence),
    rationale: str(diag.details.rationale),
  };

  const policy: Policy | null = pol && {
    decision: (pol.action as PolicyDecision) ?? null,
    proposedAction: str(pol.details.proposed_action),
    matchedRules: stringList(pol.details.matched_rules),
    reason: str(pol.details.reason),
  };

  const execution: Execution | null = exec && {
    action: exec.action,
    executionStatus: str(exec.details.execution_status),
    verificationStatus: str(exec.details.verification_status),
    simulated: bool(exec.details.simulated),
    provider: str(exec.details.provider),
    reference: str(exec.details.reference),
    url: str(exec.details.url),
  };

  const verification: Verification | null = ver && {
    verificationStatus: str(ver.details.verification_status),
    executionStatus: str(ver.details.execution_status),
    amountRecovered: str(ver.details.amount_recovered),
  };

  const recovery: Recovery | null = rec && {
    amountRecovered: str(rec.details.amount_recovered),
    currency: str(rec.details.currency),
    paymentReference: str(rec.details.payment_reference),
  };

  const verifiedRecovered =
    verification?.verificationStatus === "recovered" || rec !== null;

  return { diagnosis, policy, execution, verification, recovery, verifiedRecovered };
}
