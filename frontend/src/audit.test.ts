import { describe, expect, it } from "vitest";
import { summarizeAudit } from "./audit";
import type { AuditEntry } from "./types";

function entry(stage: string, action: string | null, details: Record<string, unknown>): AuditEntry {
  return {
    seq: 1,
    stage,
    actor: "system",
    action,
    recorded_at: "2026-09-04T06:00:00+00:00",
    correlation_id: null,
    details,
  };
}

describe("summarizeAudit", () => {
  it("summarises a Gemini diagnosis truthfully", () => {
    const s = summarizeAudit(
      entry("diagnosis", "retry_payment", { provider: "gemini", confidence: 0.9, rationale: "transient decline" }),
    );
    expect(s.headline).toBe("Gemini recommended Retry Payment");
    expect(s.lines).toContain("90% confidence");
    expect(s.tone).toBe("advisory");
  });

  it("summarises a mock diagnosis as Mock, never Gemini", () => {
    const s = summarizeAudit(entry("diagnosis", "retry_payment", { provider: "mock", confidence: 0.8 }));
    expect(s.headline).toMatch(/Mock \(offline\)/);
    expect(s.headline).not.toMatch(/gemini/i);
  });

  it("summarises an AI failure as fail-closed", () => {
    const s = summarizeAudit(entry("diagnosis", "recommend_escalation", { provider: "gemini", ai_failure: true, failure_kind: "quota" }));
    expect(s.headline).toMatch(/unavailable/i);
    expect(s.tone).toBe("warn");
  });

  it("summarises a policy approval with rule and cap", () => {
    const s = summarizeAudit(entry("policy_decision", "approve", { matched_rules: ["approve.permitted_action"], reason: "ok" }));
    expect(s.headline).toBe("APPROVE");
    expect(s.lines.join(" ")).toMatch(/approve.permitted_action/);
    expect(s.tone).toBe("authority");
  });

  it("labels a real Razorpay execution as Razorpay Test Mode", () => {
    const s = summarizeAudit(entry("execution", "create_payment_link", { provider: "razorpay_test", simulated: false, reference: "plink_x", url: "https://rzp.io/rzp/x" }));
    expect(s.headline).toMatch(/Razorpay . Test Mode/);
    expect(s.headline).not.toMatch(/demo|mock/i);
    expect(s.lines.join(" ")).toMatch(/plink_x/);
  });

  it("labels a demo execution as a test double, never Razorpay", () => {
    const s = summarizeAudit(entry("execution", "create_payment_link", { provider: "mock", simulated: true, reference: "sim_link_x" }));
    expect(s.headline).toMatch(/Demo test double/i);
    expect(s.headline).not.toMatch(/razorpay/i);
  });

  it("does not claim recovery on a pending verification", () => {
    const s = summarizeAudit(entry("verification", "create_payment_link", { verification_status: "pending", amount_recovered: "0" }));
    expect(s.headline).toMatch(/awaiting/i);
    expect(s.headline).not.toMatch(/recovered/i);
  });

  it("reports a verified recovery amount", () => {
    const s = summarizeAudit(entry("verification", "retry_payment", { verification_status: "recovered", amount_recovered: "1499.00", currency: "INR" }));
    expect(s.headline).toMatch(/verified/i);
    expect(s.headline).toMatch(/1,499/);
    expect(s.tone).toBe("good");
  });

  it("summarises the recovered outcome", () => {
    const s = summarizeAudit(entry("recovery_result", "recovered", { amount_recovered: "1499.00", currency: "INR", payment_reference: "order_x" }));
    expect(s.stageLabel).toBe("Outcome");
    expect(s.headline).toMatch(/RECOVERED/);
    expect(s.tone).toBe("good");
  });
});
