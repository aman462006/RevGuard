import { useState } from "react";
import { api } from "../api";
import type { CaseSummary } from "../types";
import { money, workflowLabel } from "../format";
import { RiskBadge, StatusBadge } from "./Badges";
import { ProvenanceTag } from "./Provenance";
import { Empty, ErrorBox, Loading } from "./states";

interface Props {
  cases: CaseSummary[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  onSelect: (caseId: string) => void;
  selectedId: string | null;
}

export function CasesTable({ cases, loading, error, onRetry, onSelect, selectedId }: Props) {
  const [resetting, setResetting] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  // Clears all cases, events, and audit history so the board can be run from scratch.
  async function handleReset() {
    if (
      !window.confirm(
        "Delete ALL cases, events, and audit history so you can run the model from scratch? " +
          "This cannot be undone.",
      )
    )
      return;
    setResetting(true);
    setResetError(null);
    try {
      await api.reset();
      onRetry(); // reloads cases + metrics
    } catch (e) {
      setResetError(e instanceof Error ? e.message : "Reset failed.");
    } finally {
      setResetting(false);
    }
  }

  if (loading && cases.length === 0) return <Loading label="Loading cases…" />;
  if (error && cases.length === 0) return <ErrorBox message={error} onRetry={onRetry} />;

  return (
    <div>
      <div className="section-head between">
        <div>
          <div className="eyebrow">Operations</div>
          <h2 className="section-title">Revenue-risk cases</h2>
          <p className="muted small">
            Every case RevGuard has detected, with its recovery status. Select a row to inspect the
            full lifecycle.
          </p>
        </div>
        <div className="reset-control">
          <button
            className="btn small danger"
            onClick={handleReset}
            disabled={resetting || (loading && cases.length === 0)}
          >
            {resetting ? "Resetting…" : "Reset all cases"}
          </button>
          <p className="muted small reset-note">
            Deletes all cases, events &amp; audit history so you can run the model from scratch.
          </p>
          {resetError && (
            <p className="err small reset-note" role="alert">
              {resetError}
            </p>
          )}
        </div>
      </div>

      {cases.length === 0 ? (
        <Empty>
          No cases yet. Create one from the <strong>Demo</strong> tab.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="grid clickable">
            <thead>
              <tr>
                <th>Case</th>
                <th>Workflow</th>
                <th>Risk</th>
                <th className="num">Revenue at Risk</th>
                <th className="num">Recovered</th>
                <th>Status</th>
                <th className="num">Attempts</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr
                  key={c.case_id}
                  className={c.case_id === selectedId ? "selected" : ""}
                  onClick={() => onSelect(c.case_id)}
                  tabIndex={0}
                  role="button"
                  aria-label={`Open case ${c.case_id}`}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(c.case_id);
                    }
                  }}
                >
                  <td className="cell-id">
                    {c.case_id}
                    {c.is_synthetic && (
                      <ProvenanceTag
                        provenance={c.provenance}
                        synthetic
                        className="prov-inline"
                        title="Synthetic demo case — not real customer money or a real merchant."
                      />
                    )}
                  </td>
                  <td>{workflowLabel(c.case_type)}</td>
                  <td>
                    <RiskBadge level={c.risk_level} />
                  </td>
                  <td className="num">{money(c.amount_at_risk, c.currency)}</td>
                  <td className="num">{money(c.amount_recovered, c.currency)}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="num">{c.attempt_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
