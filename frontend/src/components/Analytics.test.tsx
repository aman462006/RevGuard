import { render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RecoveryAnalytics } from "../types";
import { Analytics } from "./Analytics";

const analyticsMock = vi.fn();
vi.mock("../api", () => ({
  api: { analytics: () => analyticsMock() },
}));

const ANALYTICS: RecoveryAnalytics = {
  total_cases: 3,
  total_attempts: 4,
  total_recovered_amount: "3500.00",
  overall_recovery_rate: "0.5000",
  by_action: [
    {
      action: "retry_payment",
      attempts: 2,
      recoveries: 2,
      recovered_amount: "3500.00",
      recovery_rate: "1.0000",
    },
    {
      action: "send_reminder",
      attempts: 2,
      recoveries: 0,
      recovered_amount: "0.00",
      recovery_rate: "0.0000",
    },
  ],
  outcomes: {
    recovered: 1,
    escalated: 1,
    stopped: 0,
    failed: 0,
    pending: 1,
    recovered_amount: "3500.00",
    at_risk_amount: "7000.00",
    pending_amount: "1500.00",
  },
  timeline: [
    { date: "2026-03-01", recovered_amount: "1000.00", recovered_count: 1, attempts: 1 },
    { date: "2026-03-02", recovered_amount: "2500.00", recovered_count: 1, attempts: 1 },
  ],
};

afterEach(() => {
  analyticsMock.mockReset();
});

describe("Analytics", () => {
  it("surfaces the four main outcomes and per-action effectiveness", async () => {
    analyticsMock.mockResolvedValue(ANALYTICS);
    render(<Analytics />);

    // Outcome cards (scope to the outcome label to avoid the table's "Recovered" column header).
    await screen.findByText("Recovered", { selector: ".outcome-label" });
    expect(screen.getByText("Escalated")).toBeInTheDocument();
    expect(screen.getByText("Stopped")).toBeInTheDocument();
    expect(screen.getByText("Pending")).toBeInTheDocument();

    // Intervention table shows recoveries + recovered amount per action, not only counts.
    const retryRow = screen.getByText("Retry Payment").closest("tr")!;
    expect(within(retryRow).getByText("100.0%")).toBeInTheDocument();
    const reminderRow = screen.getByText("Send Reminder").closest("tr")!;
    expect(within(reminderRow).getByText("0.0%")).toBeInTheDocument();

    // Time-based view renders a bucket per day.
    expect(screen.getByText("Mar 1")).toBeInTheDocument();
    expect(screen.getByText("Mar 2")).toBeInTheDocument();
  });

  it("shows an empty state when there is no recovery activity", async () => {
    analyticsMock.mockResolvedValue({
      ...ANALYTICS,
      total_cases: 0,
      by_action: [],
      timeline: [],
    });
    render(<Analytics />);
    await waitFor(() =>
      expect(screen.getByText(/No recovery activity yet/i)).toBeInTheDocument(),
    );
  });
});
