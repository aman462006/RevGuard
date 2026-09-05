import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { BatchMetrics } from "../types";
import { Overview } from "./Overview";

const METRICS: BatchMetrics = {
  strategy: "revguard",
  total_cases: 3,
  revenue_at_risk: "100000.00",
  recovered_amount: "40000.00",
  unresolved_amount: "60000.00",
  recovery_rate: "0.4000",
  recovered: 1,
  escalated: 1,
  stopped: 0,
  failed: 0,
  open_cases: 1,
  unresolved_cases: 2,
  by_workflow: {
    failed_subscription: {
      workflow: "failed_subscription",
      total_cases: 2,
      revenue_at_risk: "10000.00",
      recovered_amount: "40000.00",
      unresolved_amount: "0.00",
      recovery_rate: "1.0000",
      recovered: 1,
      escalated: 0,
      stopped: 0,
      failed: 0,
      open_cases: 1,
    },
  },
};

describe("Overview", () => {
  it("renders the headline tiles from metrics", () => {
    render(<Overview metrics={METRICS} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText("Revenue at Risk")).toBeInTheDocument();
    expect(screen.getByText("40.0%")).toBeInTheDocument(); // recovery rate
    expect(screen.getByText("Escalated")).toBeInTheDocument();
  });

  it("shows an empty state when there are no cases", () => {
    const empty = { ...METRICS, total_cases: 0 };
    render(<Overview metrics={empty} loading={false} error={null} onRetry={() => {}} />);
    expect(screen.getByText(/No recovery cases yet/i)).toBeInTheDocument();
  });

  it("shows an error state with no data", () => {
    render(
      <Overview metrics={null} loading={false} error="boom" onRetry={() => {}} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
  });
});
