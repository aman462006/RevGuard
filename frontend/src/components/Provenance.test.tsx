import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CaseProvenance } from "../types";
import { ProvenancePanel, ProvenanceTag } from "./Provenance";

describe("ProvenanceTag", () => {
  it("labels synthetic data as Synthetic with the synthetic style", () => {
    const { container } = render(
      <ProvenanceTag provenance="synthetic" synthetic />,
    );
    const tag = container.querySelector(".prov-tag");
    expect(tag).toHaveTextContent("Synthetic");
    expect(tag).toHaveClass("synthetic");
    expect(tag).not.toHaveClass("real");
  });

  it("never marks real (internal) data as synthetic", () => {
    const { container } = render(
      <ProvenanceTag provenance="internal" synthetic={false} />,
    );
    const tag = container.querySelector(".prov-tag");
    expect(tag).toHaveClass("real");
    expect(tag).not.toHaveClass("synthetic");
    expect(tag).not.toHaveTextContent("Synthetic");
  });

  it("labels real razorpay data as Razorpay", () => {
    render(<ProvenanceTag provenance="razorpay" synthetic={false} />);
    expect(screen.getByText("Razorpay")).toBeInTheDocument();
  });
});

const PROVENANCE: CaseProvenance = {
  case: { label: "Synthetic", synthetic: true, detail: "Generated demo data." },
  transaction: { label: "Synthetic", synthetic: true, detail: "Generated demo data." },
  recovery_action: {
    label: "Razorpay Test Mode",
    synthetic: false,
    detail: "Real Razorpay Test Mode object.",
  },
  payment_verification: {
    label: "Not yet verified",
    synthetic: false,
    detail: "Recovery not verified.",
  },
};

describe("ProvenancePanel", () => {
  it("shows all four provenance facets with their labels", () => {
    render(<ProvenancePanel provenance={PROVENANCE} />);
    expect(screen.getByText("Case")).toBeInTheDocument();
    expect(screen.getByText("Transaction data")).toBeInTheDocument();
    expect(screen.getByText("Recovery action")).toBeInTheDocument();
    expect(screen.getByText("Payment verification")).toBeInTheDocument();
    expect(screen.getByText("Razorpay Test Mode")).toBeInTheDocument();
  });
});
