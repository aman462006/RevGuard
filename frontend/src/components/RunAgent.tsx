import { useState } from "react";
import { api } from "../api";
import { buildJourney, providerLabel, type JourneyStep } from "../journey";
import { derivePipeline } from "../pipeline";
import { SCENARIOS, type Scenario } from "../scenarios";
import { useAgentRunner, type RunPhase } from "../useAgentRunner";
import type { StatusInfo } from "../types";
import { AgentJourney } from "./AgentJourney";

interface Props {
  status: StatusInfo | null;
  onChanged: () => void;
  onSelect: (caseId: string) => void;
}

function phaseSteps(demo: boolean, aiLabel: string): { phase: RunPhase; label: string }[] {
  return [
    { phase: "detecting", label: "Detecting revenue risk" },
    { phase: "diagnosing", label: demo ? "Diagnosing (demo test double)" : `Diagnosing with ${aiLabel}` },
    { phase: "policy", label: "Checking recovery policy" },
    { phase: "executing", label: "Executing bounded action" },
    { phase: "verifying", label: "Verifying payment" },
    { phase: "done", label: "Outcome recorded" },
  ];
}

const PHASE_ORDER: RunPhase[] = [
  "idle",
  "detecting",
  "diagnosing",
  "policy",
  "executing",
  "verifying",
  "done",
];

export function RunAgent({ status, onChanged, onSelect }: Props) {
  const canRun = status == null ? true : status.demo || status.run_ready;
  const { phase, busyKey, error, run } = useAgentRunner(canRun);
  const [journey, setJourney] = useState<JourneyStep[] | null>(null);
  const [resultCaseId, setResultCaseId] = useState<string | null>(null);
  const [resultStatus, setResultStatus] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  async function renderCase(caseId: string) {
    const [detail, audit] = await Promise.all([api.getCase(caseId), api.getAudit(caseId)]);
    setJourney(buildJourney(detail, derivePipeline(audit.entries)));
    setResultCaseId(caseId);
    setResultStatus(detail.status);
  }

  async function onRun(s: Scenario) {
    setJourney(null);
    setResultCaseId(null);
    setResultStatus(null);
    setNote(null);
    const outcome = await run(s);
    onChanged();
    if (!outcome) return;
    if (outcome.primaryCaseId == null) {
      setNote("Events posted, but the detector did not raise a case this time.");
      return;
    }
    if (!outcome.ran && !canRun) {
      setResultCaseId(outcome.primaryCaseId);
      setNote("Case created. Live run needs configuration — open it from Cases to run later.");
      return;
    }
    if (!outcome.ran) {
      // The matching case was already terminal (e.g. re-clicked); show its recorded result.
      setNote("This scenario's case has already completed — showing its recorded result below.");
    }
    // Pull the real, authoritative case + audit and render the journey from it.
    try {
      await renderCase(outcome.primaryCaseId);
    } catch {
      setResultCaseId(outcome.primaryCaseId);
    }
  }

  // DEMO ONLY: rehearse the two WAITING outcomes as clearly-labelled deterministic test doubles.
  async function simulatePaid() {
    if (!resultCaseId) return;
    setConfirming(true);
    setNote(null);
    try {
      await api.simulatePayment(resultCaseId);
      await renderCase(resultCaseId);
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Payment simulation failed.");
    } finally {
      setConfirming(false);
    }
  }

  async function markUnpaidResult() {
    if (!resultCaseId) return;
    setConfirming(true);
    setNote(null);
    try {
      await api.markUnpaid(resultCaseId);
      await renderCase(resultCaseId);
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Verification failed.");
    } finally {
      setConfirming(false);
    }
  }

  const demo = status?.demo ?? false;
  const aiLabel = providerLabel(status?.ai_provider);
  const activeIdx = PHASE_ORDER.indexOf(phase);
  const PHASE_STEPS = phaseSteps(demo, aiLabel);

  return (
    <section className="section run-agent">
      <div className="section-head">
        <div className="eyebrow">Run the Recovery Agent</div>
        <h2 className="section-title">Put the agent to work</h2>
        <p className="muted small">
          Each scenario posts a <strong>synthetic</strong> revenue-risk signal and runs the full
          agent pipeline on the backend — nothing here is simulated in the browser.
          {demo
            ? " Demo mode: the diagnosis, execution, and verification use offline test doubles (no Razorpay call)."
            : ` Production mode: ${aiLabel} diagnoses and approved actions create real Razorpay Test Mode objects.`}
        </p>
      </div>

      <div className="workflow-grid">
        {SCENARIOS.map((s) => (
          <div className="workflow-card" key={s.key}>
            <span className="wf-kicker">Revenue risk detected</span>
            <h3>{s.title}</h3>
            <p className="wf-detected">{s.detected}</p>
            <dl className="wf-meta">
              <div>
                <dt>Signal</dt>
                <dd>{s.detected}</dd>
              </div>
              <div>
                <dt>At risk</dt>
                <dd className="wf-amount">{s.amountLabel}</dd>
              </div>
              <div>
                <dt>Intervention</dt>
                <dd>{s.intervention}</dd>
              </div>
              <div>
                <dt>Policy boundary</dt>
                <dd>{s.expect}</dd>
              </div>
            </dl>
            <p className="wf-why">{s.why}</p>
            <div className="wf-foot">
              <span className="wf-expect">Bounded workflow</span>
              <button
                className="btn primary"
                disabled={busyKey !== null}
                onClick={() => onRun(s)}
              >
                {busyKey === s.key ? "Running…" : "Create & Run"}
              </button>
            </div>
          </div>
        ))}
      </div>

      {(busyKey !== null || phase !== "idle") && !journey && (
        <div className="pipeline-strip">
          {PHASE_STEPS.map((p) => {
            const idx = PHASE_ORDER.indexOf(p.phase);
            const state =
              phase === "error"
                ? "pending"
                : idx < activeIdx
                  ? "done"
                  : idx === activeIdx
                    ? "active"
                    : "pending";
            return (
              <div className={`pipeline-phase ${state}`} key={p.phase}>
                <span className="pp-dot" />
                <span className="pp-label">{p.label}</span>
              </div>
            );
          })}
        </div>
      )}

      {error && <p className="err">{error}</p>}
      {note && !error && <p className="muted note">{note}</p>}

      {journey && resultCaseId && (
        <div className="result-panel">
          <div className="result-head">
            <div className="eyebrow muted" style={{ margin: 0 }}>
              Agent result — real backend run
            </div>
            <button className="btn small" onClick={() => onSelect(resultCaseId)}>
              View full case
            </button>
          </div>
          <div className="result-body">
            <AgentJourney steps={journey} />
          </div>

          {resultStatus === "waiting" &&
            (demo ? (
              <div className="result-cta">
                <span className="muted small">
                  Executed as a deterministic demo test double — creating a link is not recovery.
                  Rehearse an outcome: simulate the payment, or leave it unpaid to see the bounded
                  retry / policy path.
                </span>
                <div className="btn-row">
                  <button className="btn primary" onClick={simulatePaid} disabled={confirming}>
                    {confirming ? "Working…" : "Simulate payment received — demo only"}
                  </button>
                  <button className="btn" onClick={markUnpaidResult} disabled={confirming}>
                    Leave unpaid &amp; verify
                  </button>
                </div>
              </div>
            ) : (
              <div className="result-cta">
                <span className="muted small">
                  Executed against Razorpay Test Mode — creating the link is not recovery. Recovery
                  is confirmed only by Razorpay's verified paid status (webhook); no action here
                  fabricates it. Open the case to view the live provider state.
                </span>
                <button className="btn" onClick={() => onSelect(resultCaseId)}>
                  Open case
                </button>
              </div>
            ))}
          {resultStatus === "escalated" && (
            <div className="result-cta">
              <span className="muted small">
                The PolicyEngine escalated this to a human. Open the case to review and authorize
                an action.
              </span>
              <button className="btn" onClick={() => onSelect(resultCaseId)}>
                Open human review
              </button>
            </div>
          )}
        </div>
      )}
      {!journey && resultCaseId && (
        <div className="btn-row" style={{ marginTop: 16 }}>
          <button className="btn" onClick={() => onSelect(resultCaseId)}>
            Open case {resultCaseId}
          </button>
        </div>
      )}
    </section>
  );
}
