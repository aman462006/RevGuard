import { money } from "../format";
import type { Dimension, RootCause as RootCauseData } from "../rootcause";
import { titleCase } from "../format";

function pct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function DimensionBlock({ dim, currency }: { dim: Dimension; currency: string }) {
  if (!dim.available) {
    return (
      <div className="rc-dim">
        <div className="rc-dim-head">
          <span className="rc-dim-label">{dim.label}</span>
          <span className="rc-unavailable">No data</span>
        </div>
      </div>
    );
  }
  return (
    <div className="rc-dim">
      <div className="rc-dim-head">
        <span className="rc-dim-label">{dim.label}</span>
        <span className="rc-dim-note">
          dominant {pct(dim.dominantShare)}
          {dim.coverage != null && dim.coverage < 1 ? ` · ${pct(dim.coverage)} tagged` : ""}
        </span>
      </div>
      <table className="rc-table">
        <tbody>
          {dim.segments.map((s) => (
            <tr key={s.value} className={s.value === dim.dominant ? "rc-dominant" : ""}>
              <td className="rc-seg">{titleCase(s.value)}</td>
              <td className="rc-fail num">{s.failures}</td>
              <td className="rc-amt num">{money(s.amount, currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Renders the payment-degradation root-cause breakdown so the operator can see *why* RevGuard
// chose its intervention. All figures come from the detector's real segmentation.
export function RootCause({ rc, currency }: { rc: RootCauseData; currency: string }) {
  return (
    <section className="detail-section root-cause">
      <h3>
        Root cause
        <span className="tag tag-authority">degradation</span>
      </h3>

      <div className="rc-headline">
        <div className="rc-metric">
          <span className="rc-metric-label">Observed failure rate</span>
          <span className="rc-metric-value accent">{pct(rc.observedFailureRate)}</span>
        </div>
        <div className="rc-metric">
          <span className="rc-metric-label">Baseline</span>
          <span className="rc-metric-value">{pct(rc.baselineFailureRate)}</span>
        </div>
        <div className="rc-metric">
          <span className="rc-metric-label">Failures analyzed</span>
          <span className="rc-metric-value">{rc.totalFailures}</span>
        </div>
      </div>

      {rc.summary && <p className="rc-summary">{rc.summary}</p>}

      {rc.primaryDimension && rc.primarySegment && (
        <p className="rc-primary">
          Dominant driver:{" "}
          <strong>
            {titleCase(rc.primaryDimension)} = {titleCase(rc.primarySegment)}
          </strong>{" "}
          ({pct(rc.primaryShare)} of failures)
        </p>
      )}

      <div className="rc-dims">
        {rc.dimensions.map((d) => (
          <DimensionBlock key={d.key} dim={d} currency={currency} />
        ))}
      </div>

      {rc.mitigation && (
        <div className="rc-mitigation">
          <span className="rc-mitigation-label">Suggested mitigation</span>
          <span>{rc.mitigation}</span>
        </div>
      )}
      <p className="note tight">
        The detector segments the failures; the AI explains and recommends; the PolicyEngine
        authorizes. RevGuard never auto-disables a payment method.
      </p>
    </section>
  );
}
