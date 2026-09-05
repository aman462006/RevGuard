import { describe, expect, it } from "vitest";
import { pickPrimary } from "./useAgentRunner";
import { SCENARIOS } from "./scenarios";
import type { CaseSummary } from "./types";

function summary(over: Partial<CaseSummary>): CaseSummary {
  return {
    case_id: "c",
    case_type: "failed_subscription",
    status: "detected",
    is_terminal: false,
    customer_id: null,
    risk_level: "low",
    currency: "INR",
    amount_at_risk: "1000.00",
    amount_recovered: "0.00",
    attempt_count: 0,
    updated_at: "2026-01-01T00:00:00Z",
    provenance: "synthetic",
    is_synthetic: true,
    ...over,
  };
}

const overdueScenario = SCENARIOS.find((s) => s.key === "overdue")!;

describe("pickPrimary", () => {
  it("selects the case matching the clicked workflow, ignoring stale cases of other workflows", () => {
    const cases = [
      summary({ case_id: "stale_deg", case_type: "payment_degradation", status: "waiting" }),
      summary({ case_id: "stale_sub", case_type: "failed_subscription", status: "waiting" }),
      summary({ case_id: "new_overdue", case_type: "overdue_receivable", status: "detected" }),
    ];
    expect(pickPrimary(cases, overdueScenario)?.case_id).toBe("new_overdue");
  });

  it("prefers a freshly-detected case over an older open one of the same workflow", () => {
    const cases = [
      summary({ case_id: "old_overdue", case_type: "overdue_receivable", status: "waiting" }),
      summary({ case_id: "new_overdue", case_type: "overdue_receivable", status: "detected" }),
    ];
    expect(pickPrimary(cases, overdueScenario)?.case_id).toBe("new_overdue");
  });

  it("returns null when nothing was raised", () => {
    expect(pickPrimary([], overdueScenario)).toBeNull();
  });
});
