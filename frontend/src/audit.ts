// Turns a structured audit entry into a human-readable summary for the Case Detail timeline.
// The append-only structured trail stays the engineering source of truth (shown verbatim under
// a "Technical details" disclosure); this only derives a readable headline + supporting lines so
// a reviewer understands the lifecycle without reading JSON. Pure and unit-tested.

import { dateTime, money, titleCase } from "./format";
import { executionProviderLabel, providerLabel } from "./journey";
import type { AuditEntry } from "./types";

export type AuditTone = "neutral" | "good" | "warn" | "bad" | "advisory" | "authority";

export interface AuditSummary {
  stageLabel: string; // Detected, Diagnosis, Policy, Execution, Verification, Outcome, …
  headline: string; // the one-line human summary
  lines: string[]; // supporting detail
  tone: AuditTone;
}

function s(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}
function nfinite(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
function ruleList(v: unknown): string {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").join(", ") : "";
}

export function summarizeAudit(e: AuditEntry): AuditSummary {
  const d = e.details ?? {};
  const cur = s(d.currency) ?? "INR";

  switch (e.stage) {
    case "case_created":
      return {
        stageLabel: "Detected",
        headline: "Revenue-risk case created",
        lines: [
          e.action ? titleCase(e.action) : "",
          s(d.amount_at_risk) ? `${money(s(d.amount_at_risk)!, cur)} at risk` : "",
          s(d.risk_level) ? `Risk: ${s(d.risk_level)!.toUpperCase()}` : "",
        ].filter(Boolean),
        tone: "neutral",
      };

    case "diagnosis": {
      if (d.ai_failure) {
        return {
          stageLabel: "Diagnosis",
          headline: "AI diagnosis unavailable — failing closed",
          lines: [
            s(d.failure_kind) ? `Reason: ${titleCase(s(d.failure_kind)!)}` : "",
            "Recommending escalation to a human",
          ].filter(Boolean),
          tone: "warn",
        };
      }
      const conf = nfinite(d.confidence);
      return {
        stageLabel: "Diagnosis",
        headline: `${providerLabel(s(d.provider))} recommended ${
          e.action ? titleCase(e.action) : "an action"
        }`,
        lines: [
          conf != null ? `${Math.round(conf * 100)}% confidence` : "",
          s(d.rationale) ?? "",
        ].filter(Boolean),
        tone: "advisory",
      };
    }

    case "policy_decision": {
      const decision = (e.action ?? "").toLowerCase();
      const rules = ruleList(d.matched_rules);
      const tone: AuditTone =
        decision === "approve" ? "authority" : decision === "escalate" ? "warn" : "bad";
      return {
        stageLabel: "Policy",
        headline: decision ? decision.toUpperCase() : "Decision",
        lines: [
          rules ? `Rule: ${rules}` : "",
          d.human_override ? "Authorized by a human operator" : "",
          s(d.reason) ?? "",
        ].filter(Boolean),
        tone,
      };
    }

    case "execution": {
      if (d.rejected) {
        return {
          stageLabel: "Execution",
          headline: "Execution rejected (safety gate)",
          lines: [s(d.reason) ?? ""].filter(Boolean),
          tone: "bad",
        };
      }
      const provider = executionProviderLabel({
        simulated: typeof d.simulated === "boolean" ? d.simulated : null,
        provider: s(d.provider),
      });
      return {
        stageLabel: "Execution",
        headline: `${provider} — ${e.action ? titleCase(e.action) : "action"} executed`,
        lines: [
          s(d.reference) ? `Reference: ${s(d.reference)}` : "",
          s(d.url) ? `Link: ${s(d.url)}` : "",
          "Creating a link is not recovery — awaiting verified payment",
        ].filter(Boolean),
        tone: "neutral",
      };
    }

    case "verification": {
      const vs = s(d.verification_status);
      if (vs === "recovered") {
        return {
          stageLabel: "Verification",
          headline: `Payment verified — ${money(s(d.amount_recovered) ?? "0", cur)} received`,
          lines: s(d.source) ? [`Source: ${titleCase(s(d.source)!)}`] : [],
          tone: "good",
        };
      }
      if (vs === "pending") {
        return {
          stageLabel: "Verification",
          headline: "Awaiting payment confirmation",
          lines: ["A successful API call alone never counts as recovered"],
          tone: "neutral",
        };
      }
      return {
        stageLabel: "Verification",
        headline: "Not recovered",
        lines: [],
        tone: "bad",
      };
    }

    case "recovery_result":
      return {
        stageLabel: "Outcome",
        headline: `RECOVERED — ${money(s(d.amount_recovered) ?? "0", cur)} verified`,
        lines: [s(d.payment_reference) ? `Reference: ${s(d.payment_reference)}` : ""].filter(
          Boolean,
        ),
        tone: "good",
      };

    case "escalation":
      return {
        stageLabel: "Escalation",
        headline: "Escalated to human review",
        lines: [
          s(d.reason) ?? "",
          s(d.amount_at_risk) ? `${money(s(d.amount_at_risk)!, cur)} at risk` : "",
        ].filter(Boolean),
        tone: "warn",
      };

    case "stop":
      return {
        stageLabel: "Stop",
        headline: "Stopped by policy",
        lines: [
          s(d.stop_reason) ? titleCase(s(d.stop_reason)!) : "",
          s(d.reason) ?? "",
        ].filter(Boolean),
        tone: "bad",
      };

    case "status_change": {
      const a = e.action ?? "";
      const labels: Record<string, string> = {
        promise_recorded: "Promise-to-pay recorded",
        promise_due: "Promise came due",
        promise_pending: "Verifying promised payment",
        promise_kept: "Promise kept",
        promise_missed: "Promise missed",
        retry_scheduled: "Retry scheduled",
      };
      const lines: string[] = [];
      if (s(d.promised_at)) lines.push(`Due: ${dateTime(s(d.promised_at)!)}`);
      if (s(d.next_retry_at)) lines.push(`Next attempt: ${dateTime(s(d.next_retry_at)!)}`);
      if (s(d.amount)) lines.push(`${money(s(d.amount)!, cur)}`);
      return {
        stageLabel: "Lifecycle",
        headline: labels[a] ?? titleCase(a || "Status change"),
        lines,
        tone: a.includes("missed") ? "warn" : "neutral",
      };
    }

    default:
      return {
        stageLabel: titleCase(e.stage),
        headline: e.action ? titleCase(e.action) : titleCase(e.stage),
        lines: [],
        tone: "neutral",
      };
  }
}
