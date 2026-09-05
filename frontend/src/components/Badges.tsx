import type { CaseStatus, RiskLevel } from "../types";
import { titleCase } from "../format";
import type { PolicyDecision } from "../pipeline";

// Editorial status indicator: an uppercase label with a small square marker (no colored pills).
export function StatusBadge({ status }: { status: CaseStatus }) {
  return <span className={`ind ${status}`}>{titleCase(status)}</span>;
}

// Risk rendered as tracked text; paprika for high/critical.
export function RiskBadge({ level }: { level: RiskLevel }) {
  return <span className={`risk ${level}`}>{level.toUpperCase()}</span>;
}

// APPROVE / ESCALATE / STOP — the deterministic PolicyEngine outcome, visually distinct from
// the AI recommendation so the two are never confused.
export function DecisionBadge({ decision }: { decision: PolicyDecision }) {
  if (!decision) return <span className="ind">—</span>;
  return <span className={`ind ${decision}`}>{decision.toUpperCase()}</span>;
}
