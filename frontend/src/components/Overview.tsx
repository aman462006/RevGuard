import { useEffect, useState } from "react";
import { api } from "../api";
import { buildJourney, type JourneyStep } from "../journey";
import { derivePipeline } from "../pipeline";
import type { BatchMetrics, CaseSummary } from "../types";
import { money, percent } from "../format";
import { AgentJourney } from "./AgentJourney";
import { Empty, ErrorBox, Loading } from "./states";

interface Props {
  metrics: BatchMetrics | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  cases?: CaseSummary[];
  onSelect?: (caseId: string) => void;
}

const STAGES: { name: string; desc: string }[] = [
  { name: "Detect", desc: "Monitor revenue-risk signals and raise a case." },
  { name: "Diagnose", desc: "The AI model recommends the appropriate intervention." },
  { name: "Policy", desc: "A deterministic PolicyEngine authorizes, escalates, or stops." },
  { name: "Execute", desc: "Only authorized, bounded actions run via the provider." },
  { name: "Verify", desc: "Recovery counts only once the provider confirms payment." },
];

function Hero() {
  return (
    <section className="hero">
      <div className="hero-brand">RevGuard</div>
      <div className="hero-kicker">AI Revenue Recovery Agent</div>
      <h1 className="hero-headline">
        Detect revenue at risk. Decide the right intervention.{" "}
        <span className="accent">Recover it safely.</span>
      </h1>
      <p className="hero-lead">
        RevGuard identifies revenue at risk, determines the appropriate recovery intervention,
        executes only policy-authorized actions, and verifies whether the money was actually
        recovered.
      </p>

      <div className="pipeline">
        {STAGES.map((s, i) => (
          <div className="pstage" key={s.name}>
            <div className="pstage-num">{String(i + 1).padStart(2, "0")}</div>
            <div className="pstage-name">{s.name}</div>
            <div className="pstage-desc">{s.desc}</div>
            {i < STAGES.length - 1 && <span className="pstage-arrow">→</span>}
          </div>
        ))}
      </div>
    </section>
  );
}

function Kpis({ m }: { m: BatchMetrics }) {
  return (
    <div className="kpis">
      <div className="kpi">
        <div className="kpi-label">Revenue at Risk</div>
        <div className="kpi-value accent">{money(m.revenue_at_risk)}</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Revenue Recovered</div>
        <div className="kpi-value">{money(m.recovered_amount)}</div>
        <div className="kpi-note">Verified only</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Recovery Rate</div>
        <div className="kpi-value">{percent(m.recovery_rate)}</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Active Cases</div>
        <div className="kpi-value">{m.open_cases}</div>
      </div>
      <div className="kpi">
        <div className="kpi-label">Escalated</div>
        <div className="kpi-value">{m.escalated}</div>
      </div>
    </div>
  );
}

// Features the most recently updated case — the one the operator is working on now.
function pickFeatured(cases: CaseSummary[]): CaseSummary | null {
  if (cases.length === 0) return null;
  return [...cases].sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0];
}

function AgentActivity({
  cases,
  onSelect,
}: {
  cases: CaseSummary[];
  onSelect?: (id: string) => void;
}) {
  const [journey, setJourney] = useState<JourneyStep[] | null>(null);
  const [caseId, setCaseId] = useState<string | null>(null);

  const featuredId = pickFeatured(cases)?.case_id ?? null;

  useEffect(() => {
    let cancelled = false;
    if (!featuredId) {
      setJourney(null);
      setCaseId(null);
      return;
    }
    void (async () => {
      try {
        const [detail, audit] = await Promise.all([
          api.getCase(featuredId),
          api.getAudit(featuredId),
        ]);
        if (cancelled) return;
        setJourney(buildJourney(detail, derivePipeline(audit.entries)));
        setCaseId(featuredId);
      } catch {
        if (!cancelled) setJourney(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [featuredId]);

  if (!journey || !caseId) return null;

  return (
    <section className="section">
      <div className="section-head">
        <div className="eyebrow">Agent Activity</div>
        <div className="section-head between">
          <div>
            <h2 className="section-title">A real case, moving through the pipeline</h2>
            <p className="muted small mono">{caseId}</p>
          </div>
          {onSelect && (
            <button className="btn small" onClick={() => onSelect(caseId)}>
              View full case
            </button>
          )}
        </div>
      </div>
      <div className="activity">
        <div className="activity-body">
          <AgentJourney steps={journey} />
        </div>
      </div>
    </section>
  );
}

export function Overview({
  metrics,
  loading,
  error,
  onRetry,
  cases,
  onSelect,
}: Props) {
  if (loading && !metrics) return <Loading label="Loading metrics…" />;
  if (error && !metrics) return <ErrorBox message={error} onRetry={onRetry} />;

  const hasCases = !!metrics && metrics.total_cases > 0;

  return (
    <div>
      <Hero />

      {hasCases && metrics ? (
        <section className="section">
          <div className="eyebrow">
            Portfolio
            {cases && cases.length > 0 && (() => {
              const synthetic = cases.filter((c) => c.is_synthetic).length;
              if (synthetic === 0) return null;
              const all = synthetic === cases.length;
              return (
                <span className="prov-note">
                  <span className="prov-tag synthetic">Synthetic</span>
                  <span className="muted small">
                    {all
                      ? "Demo data — these figures are synthetic, not live customer revenue."
                      : `${synthetic} of ${cases.length} cases are synthetic demo data.`}
                  </span>
                </span>
              );
            })()}
          </div>
          <Kpis m={metrics} />
        </section>
      ) : (
        <div className="section">
          <Empty>
            No recovery cases yet. Open the <strong>Demo</strong> tab to run a scenario and watch
            the agent detect, diagnose, decide, execute, and verify.
          </Empty>
        </div>
      )}

      {cases && hasCases && <AgentActivity cases={cases} onSelect={onSelect} />}
    </div>
  );
}
