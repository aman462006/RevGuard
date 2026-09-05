// Subtle, consistent data-provenance labelling so demo/test data can never be mistaken for
// live production customer data. The tag is deliberately small and restrained (no large
// warning banners) — it sits alongside the existing status/risk indicators.

import type { CaseProvenance, DataProvenance, ProvenanceFacet } from "../types";
import { provenanceLabel } from "../format";

// A small inline chip marking a case's data origin. Only synthetic (demo) data gets the
// emphasised treatment; real data shows a quiet, neutral marker.
export function ProvenanceTag({
  provenance,
  synthetic,
  title,
  className,
}: {
  provenance: DataProvenance;
  synthetic: boolean;
  title?: string;
  className?: string;
}) {
  const label = synthetic ? "Synthetic" : provenanceLabel(provenance);
  return (
    <span
      className={`prov-tag ${synthetic ? "synthetic" : "real"}${className ? ` ${className}` : ""}`}
      title={
        title ??
        (synthetic
          ? "Synthetic demo data — not real customer money or a real merchant."
          : "Real (test-mode or live) provider/integration data.")
      }
    >
      {label}
    </span>
  );
}

// The four-facet provenance view for the case detail: case, transaction, recovery action, and
// payment verification. Each facet says what it is and whether it is synthetic/simulated.
export function ProvenancePanel({ provenance }: { provenance: CaseProvenance }) {
  const facets: { label: string; facet: ProvenanceFacet }[] = [
    { label: "Case", facet: provenance.case },
    { label: "Transaction data", facet: provenance.transaction },
    { label: "Recovery action", facet: provenance.recovery_action },
    { label: "Payment verification", facet: provenance.payment_verification },
  ];
  return (
    <section className="detail-section provenance-section">
      <h3>
        Data provenance
        <span className="tag">honest data</span>
      </h3>
      <p className="note tight">
        Where each part of this case comes from. Anything marked <em>synthetic</em> is generated
        demo/test data — it never represents real customer money or a real merchant.
      </p>
      <div className="prov-facets">
        {facets.map(({ label, facet }) => (
          <div className="prov-facet" key={label}>
            <div className="prov-facet-head">
              <span className="prov-facet-label">{label}</span>
              <span className={`prov-tag ${facet.synthetic ? "synthetic" : "real"}`}>
                {facet.label}
              </span>
            </div>
            <p className="prov-facet-detail muted small">{facet.detail}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
