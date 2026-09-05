import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StatusInfo } from "../types";
import { StatusBanner } from "./StatusBanner";

const DEMO: StatusInfo = {
  mode: "demo",
  demo: true,
  ai_provider: "mock",
  ai_configured: false,
  razorpay_configured: false,
  razorpay_test_mode: false,
  webhook_configured: false,
  run_ready: true,
};

const PROD_NOT_READY: StatusInfo = {
  mode: "production",
  demo: false,
  ai_provider: "gemini",
  ai_configured: false,
  razorpay_configured: false,
  razorpay_test_mode: false,
  webhook_configured: false,
  run_ready: false,
};

const PROD_READY: StatusInfo = {
  mode: "production",
  demo: false,
  ai_provider: "gemini",
  ai_configured: true,
  razorpay_configured: true,
  razorpay_test_mode: true,
  webhook_configured: true,
  run_ready: true,
};

describe("StatusBanner", () => {
  it("labels demo mode and never warns", () => {
    render(<StatusBanner status={DEMO} />);
    expect(screen.getByText("Demo")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("warns when the production live path is not configured", () => {
    render(<StatusBanner status={PROD_NOT_READY} />);
    expect(screen.getByText("Production")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/Live recovery is unavailable/i);
  });

  it("shows production ready with no warning", () => {
    render(<StatusBanner status={PROD_READY} />);
    expect(screen.getByText("Production")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("renders nothing when status is unknown", () => {
    const { container } = render(<StatusBanner status={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
