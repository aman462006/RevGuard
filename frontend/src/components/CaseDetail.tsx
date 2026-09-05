import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { AuditEntry, CaseDetail as CaseDetailT, StatusInfo } from "../types";
import { dateTime, money, titleCase, workflowLabel } from "../format";
import { derivePipeline } from "../pipeline";
import { buildJourney, executionProviderLabel, isSimulatedExecution, providerLabel } from "../journey";
import { summarizeAudit } from "../audit";
import { parseRootCause } from "../rootcause";
import { AgentJourney } from "./AgentJourney";
import { RootCause } from "./RootCause";
import { DecisionBadge, RiskBadge, StatusBadge } from "./Badges";
import { ProvenancePanel, ProvenanceTag } from "./Provenance";
import { ErrorBox, Loading } from "./states";
import { NOT_CONFIGURED_MESSAGE } from "../useAgentRunner";

interface Props {
  caseId: string;
  status?: StatusInfo | null;
  onClose: () => void;
  onChanged: () => void;
}

// Bounded actions a human operator may authorize on an escalated case. These map to the
// backend's executable-action whitelist; retry_payment moves money via a Razorpay Test Mode
// order (the reliable path to a verified recovery).
const HUMAN_ACTIONS: { action: string; label: string; hint: string; primary?: boolean }[] = [
  {
    action: "create_payment_link",
    label: "Send Razorpay payment link",
    hint: "Creates a real Razorpay Test Mode Payment Link for the amount at risk.",
    primary: true,
  },
  {
    action: "retry_payment",
    label: "Retry payment",
    hint: "Creates a Razorpay Test Mode order to collect the charge.",
  },
  {
    action: "send_reminder",
    label: "Send reminder",
    hint: "A softer nudge; no money moves until the customer pays.",
  },
  {
    action: "record_promise_to_pay",
    label: "Record promise to pay",
    hint: "Log a commitment to pay by a date.",
  },
];

export function CaseDetailPanel({ caseId, status, onClose, onChanged }: Props) {
  const [detail, setDetail] = useState<CaseDetailT | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [authorizing, setAuthorizing] = useState<string | null>(null);
  const [authorizeNote, setAuthorizeNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [d, a] = await Promise.all([api.getCase(caseId), api.getAudit(caseId)]);
      setDetail(d);
      setAudit(a.entries);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load case.");
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    void load();
  }, [load]);

  const run = useCallback(async () => {
    setRunning(true);
    setRunError(null);
    try {
      await api.runCase(caseId);
      await load();
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 503) {
        setRunError(NOT_CONFIGURED_MESSAGE);
      } else {
        setRunError(e instanceof Error ? e.message : "Run failed.");
      }
    } finally {
      setRunning(false);
    }
  }, [caseId, load, onChanged]);

  const authorize = useCallback(
    async (action: string) => {
      setAuthorizing(action);
      setRunError(null);
      setAuthorizeNote(null);
      try {
        const res = await api.authorizeCase(caseId, action, "operator");
        await load();
        onChanged();
        const s = res.case.status;
        setAuthorizeNote(
          s === "recovered"
            ? `Recovered ${money(res.amount_recovered, res.case.currency)} (verified).`
            : s === "waiting"
              ? "Action executed against Razorpay Test Mode — awaiting payment confirmation."
              : s === "escalated"
                ? "That action could not be completed; the case remains in human review."
                : `Case is now ${titleCase(s)}.`,
        );
      } catch (e) {
        if (e instanceof ApiError && e.status === 503) setRunError(NOT_CONFIGURED_MESSAGE);
        else setRunError(e instanceof Error ? e.message : "Authorization failed.");
      } finally {
        setAuthorizing(null);
      }
    },
    [caseId, load, onChanged],
  );

  // PRODUCTION: re-poll Razorpay's verified status (never fabricated). Paid → RECOVERED with
  // the verified amount; otherwise the case stays WAITING until the payment is actually made.
  const recheckPayment = useCallback(async () => {
    setAuthorizing("__recheck__");
    setRunError(null);
    setAuthorizeNote(null);
    try {
      const res = await api.recheckPayment(caseId);
      await load();
      onChanged();
      setAuthorizeNote(
        res.case.status === "recovered"
          ? `Razorpay confirmed payment — recovered ${money(res.amount_recovered, res.case.currency)} (verified).`
          : "No verified payment yet — still awaiting Razorpay's paid status. Complete the Test Mode checkout, then check again.",
      );
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Status re-check failed.");
    } finally {
      setAuthorizing(null);
    }
  }, [caseId, load, onChanged]);

  const checkPromise = useCallback(async () => {
    setAuthorizing("__promise__");
    setRunError(null);
    setAuthorizeNote(null);
    try {
      const res = await api.checkPromise(caseId);
      await load();
      onChanged();
      const s = res.case.promise?.status;
      setAuthorizeNote(
        s === "kept"
          ? `Promise kept — recovered ${money(res.amount_recovered, res.case.currency)} (verified).`
          : s === "missed"
            ? "Promise missed — routed to the PolicyEngine (escalated/stopped)."
            : "Promise is not yet due — the commitment still stands.",
      );
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Promise check failed.");
    } finally {
      setAuthorizing(null);
    }
  }, [caseId, load, onChanged]);

  // DEMO ONLY: rehearse the customer paying. Drives the case to RECOVERED through the same
  // verified reconciliation path a real payment uses — never a fabricated production recovery.
  const simulatePaid = useCallback(async () => {
    setAuthorizing("__paid__");
    setRunError(null);
    setAuthorizeNote(null);
    try {
      const res = await api.simulatePayment(caseId);
      await load();
      onChanged();
      setAuthorizeNote(
        res.case.status === "recovered"
          ? `Recovered ${money(res.amount_recovered, res.case.currency)} — payment verified.`
          : `Case is now ${titleCase(res.case.status)}.`,
      );
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Payment simulation failed.");
    } finally {
      setAuthorizing(null);
    }
  }, [caseId, load, onChanged]);

  // DEMO ONLY: rehearse the customer NOT paying. Records a verified NOT_RECOVERED (₹0) and lets
  // the PolicyEngine's bounded retry logic run the next attempt or a terminal STOP/ESCALATE.
  const markUnpaid = useCallback(async () => {
    setAuthorizing("__unpaid__");
    setRunError(null);
    setAuthorizeNote(null);
    try {
      const res = await api.markUnpaid(caseId);
      await load();
      onChanged();
      const s = res.case.status;
      setAuthorizeNote(
        s === "waiting"
          ? "Recorded as not paid — the agent made another bounded attempt; still awaiting payment."
          : s === "escalated"
            ? "Recorded as not paid — retries exhausted; the PolicyEngine escalated to human review (₹0 recovered)."
            : s === "stopped"
              ? "Recorded as not paid — retries exhausted; the PolicyEngine stopped the case (₹0 recovered)."
              : `Case is now ${titleCase(s)}.`,
      );
    } catch (e) {
      setRunError(e instanceof Error ? e.message : "Verification failed.");
    } finally {
      setAuthorizing(null);
    }
  }, [caseId, load, onChanged]);

  const pipeline = derivePipeline(audit);

  return (
    <aside className="drawer">
      <div className="drawer-head">
        <div>
          <div className="eyebrow">Case</div>
          <h2>{caseId}</h2>
          {detail && (
            <div className="drawer-meta">
              <StatusBadge status={detail.status} />
              <RiskBadge level={detail.risk_level} />
              <span className="muted small">{workflowLabel(detail.case_type)}</span>
              <ProvenanceTag provenance={detail.provenance} synthetic={detail.is_synthetic} />
            </div>
          )}
        </div>
        <button className="btn ghost" onClick={onClose} aria-label="Close">
          Close
        </button>
      </div>

      {loading && <Loading />}
      {error && <ErrorBox message={error} onRetry={load} />}

      {detail && !loading && (
        <div className="drawer-body">
          {!detail.is_terminal && detail.status !== "waiting" && (
            <div className="btn-row" style={{ marginBottom: 20 }}>
              <button className="btn primary" onClick={run} disabled={running}>
                {running ? "Running…" : "Run recovery"}
              </button>
            </div>
          )}
          {runError && <ErrorBox message={runError} />}

          {/* Top-line summary: workflow / status / at-risk / recovered */}
          <div className="case-summary">
            <SummaryCell label="Workflow" value={workflowLabel(detail.case_type)} />
            <SummaryCell label="Status" node={<StatusBadge status={detail.status} />} />
            <SummaryCell
              label="Amount at risk"
              value={money(detail.amount_at_risk, detail.currency)}
              tone="risk"
            />
            <SummaryCell
              label="Amount recovered"
              value={money(detail.amount_recovered, detail.currency)}
              tone={Number(detail.amount_recovered) > 0 ? "good" : undefined}
            />
          </div>

          {/* Data provenance — case / transaction / action / verification origin */}
          {detail.provenance_detail && (
            <ProvenancePanel provenance={detail.provenance_detail} />
          )}

          {/* The agent journey — the whole product story at a glance */}
          <Section title="Agent recovery journey">
            <AgentJourney steps={buildJourney(detail, pipeline)} />
            <div className="authority-principle">
              <span className="ap advisory">AI recommends</span>
              <span className="ap authority">PolicyEngine authorizes</span>
              <span className="ap">Executor acts</span>
              <span className="ap good">Verifier confirms</span>
            </div>
          </Section>

          {/* Human review queue — only for escalated cases. Closes the loop the PolicyEngine
              opened: a human operator authorizes one bounded action, which really executes. */}
          {detail.status === "escalated" && (
            <section className="detail-section human-review">
              <h3>
                Human review queue
                <span className="tag tag-human">action required</span>
              </h3>
              <p className="muted tight">
                The PolicyEngine escalated this case instead of acting autonomously
                {detail.escalation_reason ? ` (${detail.escalation_reason})` : ""}. As the human
                operator, authorize one bounded recovery action — it runs through the same
                executor + verifier, and recovery is still confirmed only by Razorpay.
              </p>
              <ConsentBanner
                doNotContact={detail.do_not_contact}
                blocked={detail.blocked_contact_actions}
              />
              <div className="human-actions">
                {HUMAN_ACTIONS.map((a) => {
                  const blocked = detail.blocked_contact_actions.includes(a.action);
                  return (
                    <button
                      key={a.action}
                      className={`btn ${a.primary && !blocked ? "primary" : ""}`}
                      disabled={authorizing !== null || blocked}
                      onClick={() => authorize(a.action)}
                      title={
                        blocked
                          ? "Blocked: customer opted out (do-not-contact). Consent is required."
                          : a.hint
                      }
                    >
                      {blocked
                        ? `${a.label} — consent required`
                        : authorizing === a.action
                          ? "Authorizing…"
                          : a.label}
                    </button>
                  );
                })}
              </div>
              {authorizeNote && <p className="ok tight">{authorizeNote}</p>}
              <p className="note tight">
                Authorizing is recorded as a <strong>human</strong> decision (actor: human) in the
                audit trail — distinct from an autonomous PolicyEngine approval.
              </p>
            </section>
          )}

          {/* Awaiting payment — the intervention executed, but creating a link/order is NOT
              recovery. In DEMO mode the two outcomes are rehearsable as clearly-labelled test
              doubles; in production, only Razorpay's verified paid status confirms recovery. */}
          {detail.status === "waiting" &&
            (status?.demo ? (
              <section className="detail-section awaiting-payment">
                <h3>
                  Awaiting payment
                  <span className="tag tag-wait">demo only</span>
                </h3>
                <p className="muted tight">
                  The recovery action executed as a{" "}
                  <strong>deterministic demo test double</strong> (no real provider). Creating a
                  link is not recovery — recovery is confirmed only by a verified payment. Rehearse
                  an outcome:
                </p>
                <div className="human-actions">
                  <button
                    className="btn primary"
                    disabled={authorizing !== null}
                    onClick={simulatePaid}
                  >
                    {authorizing === "__paid__"
                      ? "Simulating…"
                      : "Simulate payment received — demo only"}
                  </button>
                  <button
                    className="btn"
                    disabled={authorizing !== null}
                    onClick={markUnpaid}
                  >
                    {authorizing === "__unpaid__" ? "Verifying…" : "Leave unpaid & verify"}
                  </button>
                </div>
                {authorizeNote && <p className="ok tight">{authorizeNote}</p>}
                <p className="note tight">
                  Both are deterministic demo test doubles, never presented as a real Razorpay
                  payment. <strong>Simulate payment received</strong> drives the same verified
                  reconciliation path a real payment uses; <strong>Leave unpaid &amp; verify</strong>{" "}
                  records a verified <em>not recovered</em> (₹0) and lets the PolicyEngine's bounded
                  retry logic run the next attempt or a terminal STOP/ESCALATE.
                </p>
              </section>
            ) : (
              <section className="detail-section awaiting-payment">
                <h3>
                  Awaiting Razorpay payment
                  <span className="tag tag-wait">test mode</span>
                </h3>
                <p className="muted tight">
                  The recovery action executed against <strong>Razorpay Test Mode</strong>. Creating
                  the payment link is <strong>not</strong> recovery — this case moves to RECOVERED
                  only when Razorpay reports the payment as <em>paid</em>. No action here can
                  fabricate that.
                </p>
                <div className="human-actions">
                  {pipeline.execution?.url && (
                    <a
                      className="btn"
                      href={pipeline.execution.url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Razorpay Test Mode payment link
                    </a>
                  )}
                  <button
                    className="btn primary"
                    disabled={authorizing !== null}
                    onClick={recheckPayment}
                  >
                    {authorizing === "__recheck__" ? "Checking…" : "Check payment status"}
                  </button>
                </div>
                {authorizeNote && <p className="ok tight">{authorizeNote}</p>}
                <p className="note tight">
                  Complete the Test Mode checkout (no real money), then{" "}
                  <strong>Check payment status</strong> to confirm recovery from Razorpay's verified
                  paid status — or let the signature-verified <code>*.paid</code> webhook reconcile
                  it automatically. Recovery is read from Razorpay, never fabricated.
                </p>
              </section>
            ))}

          {/* 1. Revenue-risk signal / evidence */}
          <Section title="Revenue-risk signal">
            <Field label="Workflow" value={workflowLabel(detail.case_type)} />
            <Field label="Risk level" value={detail.risk_level.toUpperCase()} />
            <Field label="Customer" value={detail.customer_id ?? "—"} />
            <Field label="Amount at risk" value={money(detail.amount_at_risk, detail.currency)} />
            <Field label="Attempts" value={String(detail.attempt_count)} />
          </Section>

          {/* 1b. Payment-degradation root cause (only when the detector produced one) */}
          {(() => {
            const rc = parseRootCause(detail.signal_evidence);
            return rc ? <RootCause rc={rc} currency={detail.currency} /> : null;
          })()}

          {/* 2. AI recommendation — advisory only */}
          <Section title="AI recommendation" tag="advisory">
            <p className="advisory-note">ADVISORY — AI recommends only; it never authorizes payment.</p>
            {pipeline.diagnosis ? (
              <>
                <Field label="Provider" value={providerLabel(pipeline.diagnosis.provider)} />
                <Field
                  label="Recommended action"
                  value={pipeline.diagnosis.action ? titleCase(pipeline.diagnosis.action) : "—"}
                />
                <Field
                  label="Confidence"
                  value={
                    pipeline.diagnosis.confidence != null
                      ? `${Math.round(pipeline.diagnosis.confidence * 100)}%`
                      : "—"
                  }
                />
                <Field label="Reason" value={pipeline.diagnosis.rationale ?? "—"} wide />
              </>
            ) : (
              <p className="muted">No diagnosis yet — run the recovery workflow.</p>
            )}
          </Section>

          {/* 3. PolicyEngine decision — the deterministic gate */}
          <Section title="PolicyEngine decision" tag="authority">
            {pipeline.policy ? (
              <>
                <div className="field">
                  <span className="field-label">Decision</span>
                  <DecisionBadge decision={pipeline.policy.decision} />
                </div>
                <Field
                  label="On proposed action"
                  value={
                    pipeline.policy.proposedAction
                      ? titleCase(pipeline.policy.proposedAction)
                      : "—"
                  }
                />
                <Field
                  label="Matched rule(s)"
                  value={pipeline.policy.matchedRules.join(", ") || "—"}
                  wide
                />
                <Field label="Reason" value={pipeline.policy.reason ?? "—"} wide />
                <p className="note tight">
                  The AI only recommends; this deterministic gate authorizes, escalates, or stops.
                </p>
              </>
            ) : (
              <p className="muted">No policy decision yet.</p>
            )}
          </Section>

          {/* 4 + 5. Execution and verification */}
          <Section title="Execution & verification">
            {pipeline.execution ? (
              <>
                <Field
                  label="Executed action"
                  value={pipeline.execution.action ? titleCase(pipeline.execution.action) : "—"}
                />
                <Field label="Provider" value={executionProviderLabel(pipeline.execution)} />
                <Field
                  label="Execution status"
                  value={fmtStatus(pipeline.execution.executionStatus)}
                />
                <Field label="Provider reference" value={pipeline.execution.reference ?? "—"} />
                {pipeline.execution.url && (
                  <div className="field wide">
                    <span className="field-label">
                      {isSimulatedExecution(pipeline.execution)
                        ? "Simulated payment link (demo)"
                        : "Razorpay payment link"}
                    </span>
                    <span className="field-value">
                      <a href={pipeline.execution.url} target="_blank" rel="noreferrer">
                        {pipeline.execution.url}
                      </a>
                    </span>
                  </div>
                )}
              </>
            ) : (
              <div className="nothing-executed">
                <strong>Nothing executed.</strong>
                <p className="muted tight">
                  {pipeline.policy?.decision === "escalate"
                    ? "The PolicyEngine escalated this case before execution — a human must review it."
                    : pipeline.policy?.decision === "stop"
                      ? "The PolicyEngine stopped this case — a policy limit was reached, so no action ran."
                      : "The recovery workflow has not run for this case yet."}
                </p>
              </div>
            )}
            <hr />
            <div className="field">
              <span className="field-label">Verification</span>
              {pipeline.verifiedRecovered ? (
                <span className="ind recovered">Recovered (verified)</span>
              ) : (
                <span className="ind waiting">
                  {fmtStatus(pipeline.verification?.verificationStatus) || "Pending"}
                </span>
              )}
            </div>
            <p className="note tight">
              Recovery is confirmed <strong>only</strong> after verification — a successful API
              call alone never counts as recovered.
            </p>
          </Section>

          {/* 5b. Deterministic retry sequence — the fixed, policy-owned cadence */}
          <RetrySequence detail={detail} />

          {/* 5c. Promise-to-pay lifecycle — a commitment, verified only when due */}
          {detail.promise && (
            <PromiseLifecycle
              detail={detail}
              onCheck={checkPromise}
              checking={authorizing === "__promise__"}
              busy={authorizing !== null}
            />
          )}

          {/* 6. Recovered amount + terminal reasons */}
          <Section title="Outcome">
            <Field
              label="Recovered amount"
              value={money(detail.amount_recovered, detail.currency)}
              strong
            />
            {detail.escalation_reason && (
              <Field label="Escalation reason" value={detail.escalation_reason} wide />
            )}
            {detail.stop_reason && (
              <Field label="Stop reason" value={titleCase(detail.stop_reason)} wide />
            )}
          </Section>

          {/* 7. Complete audit timeline — human-readable, with raw structured data on demand */}
          <Section title="Audit timeline">
            <p className="note tight">
              The complete, append-only decision trail. Each step is summarised; the exact
              structured record is available under “Technical details”.
            </p>
            <ol className="timeline">
              {audit.map((e, i) => {
                const sum = summarizeAudit(e);
                return (
                  <li key={`${e.seq ?? i}`} className={`tone-${sum.tone}`}>
                    <div className="tl-head">
                      <span className={`tl-stage stage-${e.stage}`}>{sum.stageLabel}</span>
                      <span className="muted small">{e.actor}</span>
                      <span className="muted small">{dateTime(e.recorded_at)}</span>
                    </div>
                    <div className="tl-summary">{sum.headline}</div>
                    {sum.lines.map((line, j) => (
                      <div className="tl-line muted small" key={j}>
                        {line}
                      </div>
                    ))}
                    {Object.keys(e.details).length > 0 && (
                      <details className="tl-tech">
                        <summary>Technical details</summary>
                        <pre className="tl-details">{JSON.stringify(e.details, null, 2)}</pre>
                      </details>
                    )}
                  </li>
                );
              })}
            </ol>
          </Section>
        </div>
      )}
    </aside>
  );
}

function fmtStatus(value: string | null | undefined): string {
  return value ? titleCase(value) : "";
}

// Explicit consent/DND status shown before a human authorizes a contact action. When the
// customer has opted out, contact actions are blocked (the backend refuses them too).
function ConsentBanner({
  doNotContact,
  blocked,
}: {
  doNotContact: boolean;
  blocked: string[];
}) {
  if (!doNotContact) {
    return (
      <p className="consent ok-consent tight">
        <span className="consent-dot ok" aria-hidden />
        Consent: customer may be contacted. Communication actions are permitted.
      </p>
    );
  }
  return (
    <p className="consent no-consent tight" role="alert">
      <span className="consent-dot off" aria-hidden />
      Do-not-contact is set — communication actions are blocked
      {blocked.length ? ` (${blocked.map(titleCase).join(", ")})` : ""}. Non-contact actions
      remain available.
    </p>
  );
}

// The promise-to-pay lifecycle: promised date, current status, verification result, and the
// escalation/stop outcome on a miss. Recording a promise is never recovery — only a verified
// payment (checked on/after the promised time) can move the case to RECOVERED.
const PROMISE_LABEL: Record<string, string> = {
  promised: "Promised",
  pending: "Verifying payment",
  kept: "Kept (verified)",
  missed: "Missed",
};

function PromiseLifecycle({
  detail,
  onCheck,
  checking,
  busy,
}: {
  detail: CaseDetailT;
  onCheck: () => void;
  checking: boolean;
  busy: boolean;
}) {
  const p = detail.promise!;
  const open = p.status === "promised" || p.status === "pending";
  const due = new Date(p.promised_at).getTime() <= Date.now();
  const indClass = p.status === "kept" ? "recovered" : p.status === "missed" ? "escalated" : "waiting";
  return (
    <section className="detail-section promise-seq">
      <h3>
        Promise to pay
        <span className="tag tag-authority">lifecycle</span>
      </h3>
      <ol className="promise-track">
        {["promised", "pending", p.status === "missed" ? "missed" : "kept"].map((step) => (
          <li
            key={step}
            className={`ptrack ${p.status === step ? "current" : ""} ${
              step === "missed" ? "miss" : ""
            }`}
          >
            {PROMISE_LABEL[step]}
          </li>
        ))}
      </ol>
      <Field label="Promised date" value={dateTime(p.promised_at)} />
      <div className="field">
        <span className="field-label">Promise status</span>
        <span className={`ind ${indClass}`}>{PROMISE_LABEL[p.status] ?? titleCase(p.status)}</span>
      </div>
      {p.verification_reference && (
        <Field label="Verified payment ref" value={p.verification_reference} />
      )}
      {p.status === "missed" && detail.escalation_reason && (
        <Field label="Escalation" value={detail.escalation_reason} wide />
      )}
      {p.status === "missed" && detail.stop_reason && (
        <Field label="Stopped" value={titleCase(detail.stop_reason)} wide />
      )}
      <p className="note tight">
        A recorded promise is a commitment, not recovery. On or after the promised date the actual
        payment state is verified; a kept promise recovers the case, a missed one is routed through
        the PolicyEngine.
      </p>
      {open && (
        <div className="human-actions">
          <button className="btn" disabled={busy} onClick={onCheck}>
            {checking ? "Checking…" : due ? "Verify promised payment" : "Check promise status"}
          </button>
        </div>
      )}
    </section>
  );
}

// Human-readable label for a retry outcome from the audit-derived history.
function retryOutcomeLabel(outcome: string): string {
  if (outcome === "recovered") return "Recovered";
  if (outcome === "not_recovered") return "Not recovered";
  if (outcome === "pending") return "Awaiting confirmation";
  if (outcome === "executed") return "Executed";
  return titleCase(outcome);
}

// The bounded, deterministic retry cadence (immediate / +30m / +6h / +24h). Timing is enforced
// by the PolicyEngine, never the AI, so the schedule and next eligible time are shown verbatim.
function RetrySequence({ detail }: { detail: CaseDetailT }) {
  const history = detail.retry_history ?? [];
  const nextRetry = detail.next_retry_at ?? null;
  if (history.length === 0 && !nextRetry) return null;

  const max = detail.max_attempts ?? 4;
  return (
    <section className="detail-section retry-seq">
      <h3>
        Retry sequence
        <span className="tag tag-authority">deterministic</span>
      </h3>
      <p className="note tight">
        Retry timing follows a fixed schedule (immediate, +30m, +6h, +24h) enforced by the
        PolicyEngine — the AI recommends the action, never when it runs. Attempt{" "}
        {Math.min(detail.attempt_count, max)} of {max}.
      </p>

      {nextRetry && !detail.is_terminal && (
        <div className="retry-next">
          <span className="field-label">Next retry scheduled</span>
          <span className="field-value strong">{dateTime(nextRetry)}</span>
        </div>
      )}

      {history.length > 0 && (
        <ol className="retry-list">
          {history.map((r) => (
            <li key={r.attempt} className={`retry-item outcome-${r.outcome}`}>
              <span className="retry-n">#{r.attempt}</span>
              <span className="retry-action">{r.action ? titleCase(r.action) : "—"}</span>
              <span className="retry-when muted small">{dateTime(r.executed_at)}</span>
              <span className={`ind ${r.outcome === "recovered" ? "recovered" : "waiting"}`}>
                {retryOutcomeLabel(r.outcome)}
              </span>
              {r.next_retry_at && (
                <span className="retry-next-inline muted small">
                  → next {dateTime(r.next_retry_at)}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Section({
  title,
  tag,
  children,
}: {
  title: string;
  tag?: "advisory" | "authority";
  children: React.ReactNode;
}) {
  return (
    <section className="detail-section">
      <h3>
        {title}
        {tag && <span className={`tag tag-${tag}`}>{tag}</span>}
      </h3>
      {children}
    </section>
  );
}

function SummaryCell({
  label,
  value,
  node,
  tone,
}: {
  label: string;
  value?: string;
  node?: React.ReactNode;
  tone?: "risk" | "good";
}) {
  return (
    <div className="summary-cell">
      <span className="summary-label">{label}</span>
      <span className={`summary-value ${tone ?? ""}`}>{node ?? value}</span>
    </div>
  );
}

function Field({
  label,
  value,
  wide,
  strong,
}: {
  label: string;
  value: string;
  wide?: boolean;
  strong?: boolean;
}) {
  return (
    <div className={`field ${wide ? "wide" : ""}`}>
      <span className="field-label">{label}</span>
      <span className={`field-value ${strong ? "strong" : ""}`}>{value}</span>
    </div>
  );
}
