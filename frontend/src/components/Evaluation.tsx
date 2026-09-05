import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { EvaluationReport } from "../types";
import { money, percent, signedInt, signedMoney, workflowLabel } from "../format";
import { ErrorBox, Loading } from "./states";

export function Evaluation() {
  const [report, setReport] = useState<EvaluationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReport(await api.evaluation());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load evaluation.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !report)
    return <Loading label="Running benchmark on the synthetic dataset…" />;
  if (error && !report) return <ErrorBox message={error} onRetry={load} />;
  if (!report) return null;

  const { baseline: b, revguard: r, delta: d } = report.comparison;

  return (
    <section>
      <div className="section-head">
        <div className="eyebrow">
          Evaluation
          <span className="prov-note">
            <span className="prov-tag synthetic">Benchmark</span>
            <span className="muted small">
              Fixed synthetic dataset — not live Razorpay recovery.
            </span>
          </span>
        </div>
        <h2 className="section-title">Does the agent actually recover more?</h2>
        <div className="eval-flow">
          <span className="ef">Baseline</span>
          <span className="ef-arrow">→</span>
          <span className="ef authority">RevGuard Agent</span>
          <span className="ef-arrow">→</span>
          <span className="ef good">Measured outcome</span>
        </div>
      </div>

      <div className="eval-callout">
        <strong>This is a benchmark, not your live cases.</strong> It runs on a fixed{" "}
        <strong>synthetic dataset of {report.comparison.revguard.total_cases} test cases</strong>{" "}
        (seed {report.seed}) — completely separate from anything you run in the app, and{" "}
        <strong>deterministic</strong> (the numbers never change). It answers one question: on the
        exact same data, does the RevGuard agent recover more than a naïve baseline strategy? The
        figures below are that comparison — not money from your own runs (those live on the{" "}
        <em>Overview</em> and <em>Cases</em> tabs).
      </div>

      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Metric</th>
              <th className="num">Baseline</th>
              <th className="num">RevGuard</th>
              <th className="num">Delta</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Cases</td>
              <td className="num">{b.total_cases}</td>
              <td className="num">{r.total_cases}</td>
              <td className="num">—</td>
            </tr>
            <tr>
              <td>Revenue at risk</td>
              <td className="num">{money(b.revenue_at_risk)}</td>
              <td className="num">{money(r.revenue_at_risk)}</td>
              <td className="num">—</td>
            </tr>
            <tr>
              <td>Revenue recovered</td>
              <td className="num">{money(b.recovered_amount)}</td>
              <td className="num">{money(r.recovered_amount)}</td>
              <td className="num pos">{signedMoney(d.revenue_recovered_delta)}</td>
            </tr>
            <tr>
              <td>Recovery rate</td>
              <td className="num">{percent(b.recovery_rate)}</td>
              <td className="num">{percent(r.recovery_rate)}</td>
              <td className="num pos">{percent(d.recovery_rate_delta)}</td>
            </tr>
            <tr>
              <td>Recovered cases</td>
              <td className="num">{b.recovered}</td>
              <td className="num">{r.recovered}</td>
              <td className="num pos">{signedInt(d.recovered_cases_delta)}</td>
            </tr>
            <tr>
              <td>Escalated</td>
              <td className="num">{b.escalated}</td>
              <td className="num">{r.escalated}</td>
              <td className="num">{signedInt(d.escalated_delta)}</td>
            </tr>
            <tr>
              <td>Stopped</td>
              <td className="num">{b.stopped}</td>
              <td className="num">{r.stopped}</td>
              <td className="num">{signedInt(d.stopped_delta)}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="section-head" style={{ marginTop: 40 }}>
        <div className="eyebrow muted">By workflow</div>
        <h2 className="section-title">Recovery rate by workflow</h2>
      </div>
      <div className="table-wrap">
        <table className="grid">
          <thead>
            <tr>
              <th>Workflow</th>
              <th className="num">Baseline</th>
              <th className="num">RevGuard</th>
              <th className="num">RevGuard cases</th>
            </tr>
          </thead>
          <tbody>
            {Object.values(r.by_workflow)
              .filter(Boolean)
              .map((w) => {
                const bw = b.by_workflow[w!.workflow];
                return (
                  <tr key={w!.workflow}>
                    <td>{workflowLabel(w!.workflow)}</td>
                    <td className="num">{bw ? percent(bw.recovery_rate) : "n/a"}</td>
                    <td className="num">{percent(w!.recovery_rate)}</td>
                    <td className="num">
                      {w!.recovered}/{w!.total_cases}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>
      <p className="note">
        Measured on the identical seeded synthetic batch of{" "}
        {report.comparison.revguard.total_cases} cases; no claim beyond the numbers. Your own live
        runs are tracked separately on the Overview and Cases tabs.
      </p>
    </section>
  );
}
