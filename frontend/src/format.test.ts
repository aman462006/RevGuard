import { describe, expect, it } from "vitest";
import {
  isActive,
  money,
  percent,
  signedInt,
  signedMoney,
  workflowLabel,
} from "./format";

describe("money", () => {
  it("formats a decimal string as INR currency", () => {
    const out = money("1500.00");
    expect(out).toContain("1,500");
    expect(out).toMatch(/₹|INR/);
  });
  it("returns the input when not numeric", () => {
    expect(money("n/a")).toBe("n/a");
  });
});

describe("percent", () => {
  it("renders a [0,1] rate as a percentage", () => {
    expect(percent("0.7500")).toBe("75.0%");
    expect(percent("0")).toBe("0.0%");
  });
});

describe("signed helpers", () => {
  it("prefixes sign on money and ints", () => {
    expect(signedMoney("0")).toContain("+");
    expect(signedMoney("-5")).toContain("-");
    expect(signedInt(3)).toBe("+3");
    expect(signedInt(-2)).toBe("-2");
    expect(signedInt(0)).toBe("+0");
  });
});

describe("labels + status grouping", () => {
  it("maps workflow enum values to readable labels", () => {
    expect(workflowLabel("failed_subscription")).toBe("Failed subscription");
    expect(workflowLabel("overdue_receivable")).toBe("Overdue receivable");
  });
  it("treats non-terminal statuses as active and terminal as not", () => {
    expect(isActive("waiting")).toBe(true);
    expect(isActive("detected")).toBe(true);
    expect(isActive("recovered")).toBe(false);
    expect(isActive("escalated")).toBe(false);
  });
});
