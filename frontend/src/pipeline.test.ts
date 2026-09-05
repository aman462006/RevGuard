import { describe, expect, it } from "vitest";
import { derivePipeline } from "./pipeline";
import type { AuditEntry } from "./types";

function entry(stage: string, over: Partial<AuditEntry> = {}): AuditEntry {
  return {
    seq: null,
    stage,
    actor: "system",
    action: null,
    recorded_at: "2026-01-01T00:00:00Z",
    correlation_id: null,
    details: {},
    ...over,
  };
}

const RECOVERED_TRAIL: AuditEntry[] = [
  entry("case_created", { actor: "system", action: "failed_subscription" }),
  entry("diagnosis", {
    actor: "ai",
    action: "retry_payment",
    details: { provider: "gemini", confidence: 0.82, rationale: "transient decline" },
  }),
  entry("policy_decision", {
    actor: "policy",
    action: "approve",
    details: {
      proposed_action: "retry_payment",
      matched_rules: ["ai_recommendation", "amount_threshold"],
      reason: "within budget",
    },
  }),
  entry("execution", {
    actor: "executor",
    action: "retry_payment",
    details: {
      execution_status: "succeeded",
      verification_status: "pending",
      simulated: true,
      provider: "mock",
      reference: "order_1",
      amount_recovered: "0",
    },
  }),
  entry("verification", {
    actor: "system",
    action: "retry_payment",
    details: {
      verification_status: "recovered",
      execution_status: "succeeded",
      amount_recovered: "1500.00",
    },
  }),
  entry("recovery_result", {
    actor: "system",
    action: "recovered",
    details: { amount_recovered: "1500.00", currency: "INR", payment_reference: "order_1" },
  }),
];

describe("derivePipeline", () => {
  it("separates the AI recommendation from the deterministic policy decision", () => {
    const p = derivePipeline(RECOVERED_TRAIL);
    expect(p.diagnosis?.provider).toBe("gemini");
    expect(p.diagnosis?.action).toBe("retry_payment");
    expect(p.diagnosis?.confidence).toBe(0.82);
    // Policy is a distinct authority with its own decision + matched rules.
    expect(p.policy?.decision).toBe("approve");
    expect(p.policy?.matchedRules).toContain("ai_recommendation");
  });

  it("confirms recovery only when verification says so", () => {
    const p = derivePipeline(RECOVERED_TRAIL);
    expect(p.verification?.verificationStatus).toBe("recovered");
    expect(p.verifiedRecovered).toBe(true);
    expect(p.recovery?.amountRecovered).toBe("1500.00");
  });

  it("does NOT treat a successful-but-pending execution as recovered", () => {
    const trail = RECOVERED_TRAIL.slice(0, 4); // through execution only, no verification
    const p = derivePipeline(trail);
    expect(p.execution?.executionStatus).toBe("succeeded");
    expect(p.verification).toBeNull();
    expect(p.verifiedRecovered).toBe(false);
  });

  it("captures an ESCALATE decision without any execution", () => {
    const trail: AuditEntry[] = [
      entry("diagnosis", {
        actor: "ai",
        action: "recommend_escalation",
        details: { provider: "gemini", confidence: 0.6, rationale: "high value" },
      }),
      entry("policy_decision", {
        actor: "policy",
        action: "escalate",
        details: {
          proposed_action: "recommend_escalation",
          matched_rules: ["amount_threshold"],
          reason: "amount exceeds escalation threshold",
        },
      }),
      entry("escalation", { actor: "policy", action: "escalate" }),
    ];
    const p = derivePipeline(trail);
    expect(p.policy?.decision).toBe("escalate");
    expect(p.execution).toBeNull();
    expect(p.verifiedRecovered).toBe(false);
  });

  it("returns nulls for an empty trail", () => {
    const p = derivePipeline([]);
    expect(p.diagnosis).toBeNull();
    expect(p.policy).toBeNull();
    expect(p.verifiedRecovered).toBe(false);
  });
});
