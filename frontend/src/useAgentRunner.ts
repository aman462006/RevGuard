import { useCallback, useState } from "react";
import { api, ApiError } from "./api";
import type { Scenario } from "./scenarios";
import type { CaseStatus, CaseSummary, StatusInfo } from "./types";

export interface RunOutcome {
  caseIds: string[];
  primaryCaseId: string | null;
  statuses: CaseStatus[];
  ran: boolean; // whether the recovery pipeline was actually run (vs create-only)
}

// The live phases we surface while the backend pipeline runs. These describe what the *agent*
// is doing; the authoritative result always comes from the case + audit the backend returns.
export type RunPhase =
  | "idle"
  | "detecting"
  | "diagnosing"
  | "policy"
  | "executing"
  | "verifying"
  | "done"
  | "error";

export function notConfiguredMessage(status: StatusInfo | null): string {
  if (!status) {
    return (
      "Live recovery is not configured (HTTP 503). Check the backend's active AI provider " +
      "and Razorpay Test Mode credentials, or run it with REVGUARD_MODE=demo for an offline rehearsal."
    );
  }

  const missing: string[] = [];
  if (!status.ai_configured) missing.push(`${status.ai_provider} API key`);
  if (!status.razorpay_configured) missing.push("Razorpay Test Mode key/secret");
  else if (!status.razorpay_test_mode) missing.push("Razorpay Test Mode key");

  const reason = missing.length > 0 ? ` Missing: ${missing.join("; ")}.` : "";
  return (
    `Live recovery is not configured (HTTP 503).${reason} ` +
    "Run it with REVGUARD_MODE=demo for an offline rehearsal."
  );
}

export const NOT_CONFIGURED_MESSAGE = notConfiguredMessage(null);

// Posting an event makes the backend detector re-scan *all* accumulated events, so the response
// can include stale open cases from earlier runs alongside the one this scenario just raised.
// We must therefore pick the case that belongs to the workflow the user actually clicked — and,
// among those, prefer a brand-new ("detected") case over an older one still mid-flight.
export function pickPrimary(cases: CaseSummary[], scenario: Scenario): CaseSummary | null {
  const sameWorkflow = cases.filter((c) => c.case_type === scenario.workflow);
  const pool = sameWorkflow.length > 0 ? sameWorkflow : cases;
  if (pool.length === 0) return null;
  return (
    pool.find((c) => c.status === "detected") ?? // freshly raised, not yet run
    pool.find((c) => !c.is_terminal) ?? // otherwise anything still open
    pool[pool.length - 1] // else the most recent
  );
}

export function useAgentRunner(canRun: boolean, status: StatusInfo | null = null) {
  const [phase, setPhase] = useState<RunPhase>("idle");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<RunOutcome | null>(null);

  const reset = useCallback(() => {
    setPhase("idle");
    setError(null);
    setOutcome(null);
  }, []);

  const run = useCallback(
    async (scenario: Scenario): Promise<RunOutcome | null> => {
      setBusyKey(scenario.key);
      setError(null);
      setOutcome(null);
      setPhase("detecting");
      try {
        const events = scenario.build();
        const byId = new Map<string, CaseSummary>();
        for (const ev of events) {
          const res = await api.postEvent(ev);
          for (const c of res.cases) byId.set(c.case_id, c);
        }
        // Only ever act on the single case that matches the clicked workflow.
        const primary = pickPrimary([...byId.values()], scenario);
        if (primary == null) {
          setPhase("idle");
          const res: RunOutcome = { caseIds: [], primaryCaseId: null, statuses: [], ran: false };
          setOutcome(res);
          return res;
        }
        if (!canRun) {
          setPhase("done");
          const res: RunOutcome = {
            caseIds: [primary.case_id],
            primaryCaseId: primary.case_id,
            statuses: [],
            ran: false,
          };
          setOutcome(res);
          return res;
        }
        // Already-terminal case (e.g. re-clicked): don't re-run; just surface it.
        if (primary.is_terminal) {
          setPhase("done");
          const res: RunOutcome = {
            caseIds: [primary.case_id],
            primaryCaseId: primary.case_id,
            statuses: [primary.status],
            ran: false,
          };
          setOutcome(res);
          return res;
        }

        // Drive the real pipeline for exactly this case. Phase labels are cosmetic; the backend
        // does the work and returns the authoritative terminal status.
        setPhase("diagnosing");
        setPhase("policy");
        const run = await api.runCase(primary.case_id);
        const status = run.case.status;
        setPhase(status === "recovered" ? "verifying" : "policy");
        setPhase("done");
        const res: RunOutcome = {
          caseIds: [primary.case_id],
          primaryCaseId: primary.case_id,
          statuses: [status],
          ran: true,
        };
        setOutcome(res);
        return res;
      } catch (e) {
        setPhase("error");
        if (e instanceof ApiError && e.status === 503) setError(notConfiguredMessage(status));
        else setError(e instanceof Error ? e.message : "Failed to run the recovery agent.");
        return null;
      } finally {
        setBusyKey(null);
      }
    },
    [canRun],
  );

  return { phase, busyKey, error, outcome, run, reset };
}
