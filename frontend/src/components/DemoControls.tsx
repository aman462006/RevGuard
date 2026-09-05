import { useState } from "react";
import { api, ApiError } from "../api";
import { SCENARIOS, type Scenario } from "../scenarios";
import { NOT_CONFIGURED_MESSAGE, pickPrimary } from "../useAgentRunner";
import type { CaseSummary, StatusInfo } from "../types";

interface Props {
  onCreated: () => void;
  // Called once a scenario has produced a case (created / run) so the app can switch to the
  // Overview, where the agent journey for the new case is shown.
  onDone?: () => void;
  status: StatusInfo | null;
}

export function DemoControls({ onCreated, onDone, status }: Props) {
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // "Create & run" is offered when the current mode can actually run a recovery: demo (mocks)
  // or a fully-configured production path. Otherwise we create the case(s) only.
  const canRun = status == null ? true : status.demo || status.run_ready;

  async function runScenario(s: Scenario) {
    setBusy(s.key);
    setError(null);
    setMessage(null);
    try {
      const events = s.build();
      const byId = new Map<string, CaseSummary>();
      for (const ev of events) {
        const res = await api.postEvent(ev);
        for (const c of res.cases) byId.set(c.case_id, c);
      }
      // Act only on the case that belongs to *this* workflow — posting an event makes the
      // detector re-scan all events, so the response can include stale cases from earlier runs.
      const primary = pickPrimary([...byId.values()], s);
      if (primary == null) {
        setMessage(`Posted ${events.length} event(s); the detector did not raise a case.`);
        onCreated();
        return;
      }
      if (!canRun) {
        setMessage(`Created case ${primary.case_id}. Run it from Cases (live run needs config).`);
        onCreated();
        onDone?.();
        return;
      }
      if (primary.is_terminal) {
        setMessage(`Case ${primary.case_id} already completed → ${primary.status}.`);
        onCreated();
        onDone?.();
        return;
      }
      const run = await api.runCase(primary.case_id);
      const status = run.case.status;
      setMessage(
        status === "waiting"
          ? `Ran ${primary.case_id} → awaiting payment. Open it in Cases and confirm the payment to complete verified recovery (creating a link is not recovery).`
          : status === "escalated"
            ? `Ran ${primary.case_id} → escalated to human review (policy did not auto-approve execution).`
            : `Ran ${primary.case_id} → ${status}.`,
      );
      onCreated();
      // The case is ready — switch to Overview so the reviewer sees its agent journey.
      onDone?.();
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setError(NOT_CONFIGURED_MESSAGE);
      } else {
        setError(e instanceof Error ? e.message : "Failed to run scenario.");
      }
    } finally {
      setBusy(null);
    }
  }

  const runLabel = canRun ? "Create & Run" : "Create scenario";

  return (
    <section>
      <div className="section-head">
        <div className="eyebrow">
          Demo
          <span className="prov-note">
            <span className="prov-tag synthetic">Synthetic</span>
            <span className="muted small">Generated demo data — not real customer money.</span>
          </span>
        </div>
        <h2 className="section-title">Synthetic recovery scenarios</h2>
        <p className="muted small">
          {canRun
            ? "Each scenario creates a synthetic (demo) case and runs the recovery workflow through the real detector, PolicyEngine, executor, and verifier (with offline test doubles for the AI and payments in demo mode)."
            : "Each scenario creates a synthetic (demo) case through the real detector and PolicyEngine."}
          {" Cases created here are labelled "}
          <span className="prov-tag synthetic">Synthetic</span>
          {" everywhere they appear."}
          {status?.demo && " Demo mode — offline test doubles."}
        </p>
      </div>

      <div className="workflow-grid">
        {SCENARIOS.map((s) => (
          <div className="workflow-card" key={s.key}>
            <span className="wf-kicker">
              Scenario
              <span className="prov-tag synthetic prov-inline">Synthetic</span>
            </span>
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
                <dt>Expected intervention</dt>
                <dd>{s.intervention}</dd>
              </div>
              <div>
                <dt>Policy boundary</dt>
                <dd>{s.expect}</dd>
              </div>
            </dl>
            <p className="wf-why">{s.why}</p>
            <div className="wf-foot">
              <span className="wf-expect">Runs the real pipeline</span>
              <button
                className="btn primary"
                disabled={busy !== null}
                onClick={() => runScenario(s)}
              >
                {busy === s.key ? "Working…" : runLabel}
              </button>
            </div>
          </div>
        ))}
      </div>

      {message && <p className="ok note">{message}</p>}
      {error && <p className="err note">{error}</p>}
    </section>
  );
}
