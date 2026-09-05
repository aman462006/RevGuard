import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { ActivityBucket, RecoveryAnalytics } from "../types";
import { money, percent, titleCase } from "../format";
import { Empty, ErrorBox, Loading } from "./states";

// Operational analytics: which interventions recover money, the outcome mix, and the recovery
// trend over time. Every figure is deterministic and comes straight from the backend
// /analytics endpoint (persisted cases + audit log) — the UI only formats it.
export function Analytics() {
  const [data, setData] = useState<RecoveryAnalytics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.analytics());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load analytics.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !data) return <Loading label="Computing recovery analytics…" />;
  if (error && !data) return <ErrorBox message={error} onRetry={load} />;
  if (!data) return null;

  if (data.total_cases === 0) {
    return (
      <section>
        <Head />
        <div className="section">
          <Empty>
            No recovery activity yet. Run a workflow from the <strong>Demo</strong> or{" "}
            <strong>Overview</strong> tab, then return here to see which interventions recover
            money and how recovery trends over time.
          </Empty>
        </div>
      </section>
    );
  }

  const o = data.outcomes;
  return (
    <section>
      <Head />

      {/* Outcome mix — the four main outcomes, high-signal. */}
      <div className="outcome-row">
        <OutcomeCard label="Recovered" value={o.recovered} tone="good"
          sub={money(o.recovered_amount)} />
        <OutcomeCard label="Escalated" value={o.escalated} tone="warn" sub="Human review" />
        <OutcomeCard label="Stopped" value={o.stopped} tone="bad" sub="Policy limit" />
        <OutcomeCard label="Pending" value={o.pending} sub={money(o.pending_amount)} />
      </div>
      <p className="note tight">
        {data.total_cases} case(s) · {data.total_attempts} recovery attempt(s) ·{" "}
        {money(data.total_recovered_amount)} recovered ({percent(data.overall_recovery_rate)} of
        revenue at risk). Verified recoveries only.
      </p>

      {/* Which interventions actually recover money (not just case counts). */}
      <div className="section-head" style={{ marginTop: 36 }}>
        <div className="eyebrow muted">By intervention</div>
        <h2 className="section-title">Which actions recover money</h2>
        <p className="muted small">
          Attempts, verified recoveries, and recovered amount per action type — so you can see
          which interventions are effective, not only how often they ran.
        </p>
      </div>
      <ActionTable data={data} />

      {/* Recovery trend over time. */}
      <div className="section-head" style={{ marginTop: 36 }}>
        <div className="eyebrow muted">Over time</div>
        <h2 className="section-title">Recovery trend</h2>
        <p className="muted small">
          Recovered amount per day (UTC). Use it to see whether recovery is improving or
          declining.
        </p>
      </div>
      <Timeline buckets={data.timeline} />
    </section>
  );
}

function Head() {
  return (
    <div className="section-head">
      <div className="eyebrow">Analytics</div>
      <h2 className="section-title">Recovery effectiveness &amp; activity</h2>
      <p className="muted small">
        Deterministic, computed only from your persisted cases and audit trail — no AI, no
        estimates.
      </p>
    </div>
  );
}

function OutcomeCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: number;
  sub?: string;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className="outcome-card">
      <div className="outcome-label">{label}</div>
      <div className={`outcome-value ${tone ?? ""}`}>{value}</div>
      {sub && <div className="outcome-sub muted small">{sub}</div>}
    </div>
  );
}

function ActionTable({ data }: { data: RecoveryAnalytics }) {
  if (data.by_action.length === 0) {
    return (
      <Empty>No actions have executed yet — run a case to populate intervention analytics.</Empty>
    );
  }
  return (
    <div className="table-wrap">
      <table className="grid">
        <thead>
          <tr>
            <th>Intervention</th>
            <th className="num">Attempts</th>
            <th className="num">Recoveries</th>
            <th className="num">Recovered</th>
            <th>Recovery rate</th>
          </tr>
        </thead>
        <tbody>
          {data.by_action.map((r) => {
            const pct = Math.round(Number(r.recovery_rate) * 100);
            return (
              <tr key={r.action}>
                <td>{titleCase(r.action)}</td>
                <td className="num">{r.attempts}</td>
                <td className="num">{r.recoveries}</td>
                <td className="num">{money(r.recovered_amount)}</td>
                <td>
                  <div className="rate-cell">
                    <div className="rate-bar" aria-hidden>
                      <div
                        className={`rate-fill ${pct > 0 ? "on" : ""}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="rate-pct">{percent(r.recovery_rate)}</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Timeline({ buckets }: { buckets: ActivityBucket[] }) {
  if (buckets.length === 0) {
    return <Empty>No recovery activity recorded yet.</Empty>;
  }
  const max = buckets.reduce((m, b) => Math.max(m, Number(b.recovered_amount)), 0);
  return (
    <div className="timeline-chart" role="img" aria-label="Recovered amount per day">
      {buckets.map((b) => {
        const amt = Number(b.recovered_amount);
        const h = max > 0 ? Math.round((amt / max) * 100) : 0;
        return (
          <div className="tl-col" key={b.date} title={`${b.date}: ${money(b.recovered_amount)}`}>
            <div className="tl-bar-track">
              <div className={`tl-bar ${amt > 0 ? "on" : ""}`} style={{ height: `${h}%` }} />
            </div>
            <div className="tl-amount">{amt > 0 ? money(b.recovered_amount) : "—"}</div>
            <div className="tl-date muted small">{shortDate(b.date)}</div>
          </div>
        );
      })}
    </div>
  );
}

// "2026-03-01" -> "Mar 1" (locale-independent, deterministic).
const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];
function shortDate(iso: string): string {
  const [, m, d] = iso.split("-");
  const mi = Number(m) - 1;
  if (mi < 0 || mi > 11) return iso;
  return `${MONTHS[mi]} ${Number(d)}`;
}
