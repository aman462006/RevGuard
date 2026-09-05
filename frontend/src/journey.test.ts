import { describe, expect, it } from "vitest";
import { buildJourney, providerLabel } from "./journey";
import type { Pipeline } from "./pipeline";
import type { CaseDetail } from "./types";

const baseCase: CaseDetail = {
  case_id: "case_1",
  case_type: "failed_subscription",
  status: "detected",
  is_terminal: false,
  customer_id: "cust_1",
  risk_level: "medium",
  currency: "INR",
  amount_at_risk: "1500.00",
  amount_recovered: "0.00",
  attempt_count: 0,
  updated_at: "2026-01-01T00:00:00Z",
  provenance: "synthetic",
  is_synthetic: true,
  current_action: null,
  current_step: 0,
  created_at: "2026-01-01T00:00:00Z",
  escalated_at: null,
  escalation_reason: null,
  stopped_at: null,
  stop_reason: null,
  provenance_detail: {
    case: { label: "Synthetic", synthetic: true, detail: "demo" },
    transaction: { label: "Synthetic", synthetic: true, detail: "demo" },
    recovery_action: { label: "None yet", synthetic: false, detail: "none" },
    payment_verification: { label: "Not yet verified", synthetic: false, detail: "none" },
  },
  do_not_contact: false,
  blocked_contact_actions: [],
};

const emptyPipeline: Pipeline = {
  diagnosis: null,
  policy: null,
  execution: null,
  verification: null,
  recovery: null,
  verifiedRecovered: false,
};

describe("providerLabel", () => {
  it("maps known providers", () => {
    expect(providerLabel("gemini")).toBe("Gemini");
    expect(providerLabel("mock")).toBe("Mock (offline)");
    expect(providerLabel(null)).toBe("AI");
  });
});

describe("buildJourney", () => {
  it("marks detect done and later stages pending for a fresh case", () => {
    const steps = buildJourney(baseCase, emptyPipeline);
    expect(steps.map((s) => s.key)).toEqual([
      "detect",
      "diagnose",
      "policy",
      "execute",
      "verify",
      "outcome",
    ]);
    expect(steps[0].state).toBe("done");
    expect(steps[1].state).toBe("pending");
  });

  it("shows recovered outcome with verified amount", () => {
    const detail: CaseDetail = {
      ...baseCase,
      status: "recovered",
      is_terminal: true,
      amount_recovered: "1500.00",
    };
    const pipeline: Pipeline = {
      ...emptyPipeline,
      diagnosis: { provider: "gemini", action: "retry_payment", confidence: 0.9, rationale: "r" },
      policy: { decision: "approve", proposedAction: "retry_payment", matchedRules: ["r1"], reason: "ok" },
      execution: {
        action: "retry_payment",
        executionStatus: "succeeded",
        verificationStatus: "recovered",
        simulated: true,
        provider: "mock",
        reference: "pay_x",
        url: null,
      },
      verification: {
        verificationStatus: "recovered",
        executionStatus: "succeeded",
        amountRecovered: "1500.00",
      },
      verifiedRecovered: true,
    };
    const steps = buildJourney(detail, pipeline);
    const verify = steps.find((s) => s.key === "verify")!;
    const outcome = steps.find((s) => s.key === "outcome")!;
    expect(verify.state).toBe("done");
    expect(verify.tone).toBe("good");
    // Truthful: a mock/simulated execution must NOT claim Razorpay confirmation.
    expect(verify.lines.join(" ")).toMatch(/simulated verification/i);
    expect(verify.lines.join(" ")).not.toMatch(/razorpay/i);
    expect(outcome.headline).toBe("RECOVERED");
    expect(outcome.tone).toBe("good");
  });

  it("labels a real Razorpay Test Mode execution truthfully (never 'mock')", () => {
    const detail: CaseDetail = { ...baseCase, status: "waiting", current_action: "create_payment_link" };
    const pipeline: Pipeline = {
      ...emptyPipeline,
      diagnosis: { provider: "gemini", action: "create_payment_link", confidence: 0.9, rationale: "r" },
      policy: { decision: "approve", proposedAction: "create_payment_link", matchedRules: ["r1"], reason: "ok" },
      execution: {
        action: "create_payment_link",
        executionStatus: "succeeded",
        verificationStatus: "pending",
        simulated: false,
        provider: "razorpay_test",
        reference: "plink_abc",
        url: "https://rzp.io/rzp/abc",
      },
    };
    const execute = buildJourney(detail, pipeline).find((s) => s.key === "execute")!;
    const text = execute.lines.join(" ");
    expect(text).toMatch(/Razorpay . Test Mode/i);
    expect(text).not.toMatch(/demo|simulated/i);
    expect(text).toMatch(/Payment link: https:\/\/rzp\.io/);
  });

  it("labels a demo test-double execution truthfully (never 'Razorpay')", () => {
    const detail: CaseDetail = { ...baseCase, status: "waiting", current_action: "create_payment_link" };
    const pipeline: Pipeline = {
      ...emptyPipeline,
      diagnosis: { provider: "mock", action: "create_payment_link", confidence: 0.9, rationale: "r" },
      policy: { decision: "approve", proposedAction: "create_payment_link", matchedRules: ["r1"], reason: "ok" },
      execution: {
        action: "create_payment_link",
        executionStatus: "succeeded",
        verificationStatus: "pending",
        simulated: true,
        provider: "mock",
        reference: "sim_link_abc",
        url: "https://demo.revguard.local/pay/abc",
      },
    };
    const execute = buildJourney(detail, pipeline).find((s) => s.key === "execute")!;
    const text = execute.lines.join(" ");
    expect(text).toMatch(/Demo test double/i);
    expect(text).not.toMatch(/razorpay/i);
    expect(text).toMatch(/Simulated link \(demo\)/i);
  });

  it("does not execute or verify when the PolicyEngine escalates", () => {
    const detail: CaseDetail = {
      ...baseCase,
      status: "escalated",
      is_terminal: true,
      escalation_reason: "amount too high",
    };
    const pipeline: Pipeline = {
      ...emptyPipeline,
      diagnosis: { provider: "gemini", action: "retry_payment", confidence: 0.8, rationale: "r" },
      policy: { decision: "escalate", proposedAction: "retry_payment", matchedRules: ["amount_cap"], reason: "high value" },
      verifiedRecovered: false,
    };
    const steps = buildJourney(detail, pipeline);
    const execute = steps.find((s) => s.key === "execute")!;
    const verify = steps.find((s) => s.key === "verify")!;
    const outcome = steps.find((s) => s.key === "outcome")!;
    expect(execute.state).toBe("skipped");
    expect(execute.headline).toMatch(/No payment action executed/i);
    expect(verify.state).toBe("skipped");
    expect(outcome.headline).toBe("ESCALATED");
  });

  it("marks execution blocked on a policy stop", () => {
    const detail: CaseDetail = { ...baseCase, status: "stopped", is_terminal: true, stop_reason: "max_attempts" };
    const pipeline: Pipeline = {
      ...emptyPipeline,
      policy: { decision: "stop", proposedAction: "retry_payment", matchedRules: ["max_attempts"], reason: "limit" },
    };
    const steps = buildJourney(detail, pipeline);
    expect(steps.find((s) => s.key === "execute")!.state).toBe("blocked");
    expect(steps.find((s) => s.key === "outcome")!.headline).toBe("STOPPED");
  });
});
