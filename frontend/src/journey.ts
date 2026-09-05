// Builds the canonical RevGuard agent journey from a case + its derived pipeline.
//
// The whole product story is these six stages, in order:
//
//   DETECT → DIAGNOSE → POLICY → EXECUTE → VERIFY → OUTCOME
//
// Every field here comes from data the backend already records (case detail + audit trail via
// derivePipeline). Nothing is invented: if the AI has not run, DIAGNOSE is "pending"; if the
// PolicyEngine escalated/stopped, EXECUTE is explicitly marked as *not executed*; and VERIFY is
// only ever "confirmed" when the backend verification stage confirmed it. This is what lets a
// judge see — without reading code — that the AI only advises, a deterministic gate authorizes,
// and recovery is verification-based.

import { money, titleCase, workflowLabel } from "./format";
import type { Pipeline } from "./pipeline";
import type { CaseDetail } from "./types";

export type StepKey = "detect" | "diagnose" | "policy" | "execute" | "verify" | "outcome";

// done = happened; active = in flight / awaiting; skipped = deliberately not run (e.g. no
// execution after an escalation); blocked = a policy stop; pending = not reached yet.
export type StepState = "done" | "active" | "skipped" | "blocked" | "pending";

export type Tone = "neutral" | "good" | "warn" | "bad" | "advisory" | "authority";

export interface JourneyStep {
  key: StepKey;
  label: string; // DETECT, DIAGNOSE, …
  headline: string; // the one-line answer for this stage
  lines: string[]; // supporting detail
  state: StepState;
  tone: Tone;
}

export function providerLabel(provider: string | null | undefined): string {
  if (!provider) return "AI";
  const p = provider.trim().toLowerCase();
  if (p === "groq") return "Llama 3.3 (Groq)";
  if (p === "gemini") return "Gemini";
  if (p === "anthropic") return "Claude";
  if (p === "mock") return "Mock (offline)";
  return titleCase(provider);
}

// True when an execution was performed by the offline demo test double rather than a real
// Razorpay Test Mode call. Driven by the audited ground-truth (provider name + simulated flag).
export function isSimulatedExecution(e: {
  simulated: boolean | null;
  provider: string | null;
}): boolean {
  return e.simulated === true || e.provider === "mock";
}

// Truthful, human label for the provider that actually executed an action. Never claims
// Razorpay when a demo test double ran, and never claims a mock when Razorpay ran.
export function executionProviderLabel(e: {
  simulated: boolean | null;
  provider: string | null;
}): string {
  return isSimulatedExecution(e) ? "Demo test double (simulated)" : "Razorpay · Test Mode";
}

function confidencePct(confidence: number | null): string | null {
  return confidence != null ? `${Math.round(confidence * 100)}% confidence` : null;
}

export function buildJourney(detail: CaseDetail, pipeline: Pipeline): JourneyStep[] {
  const { diagnosis, policy, execution, verifiedRecovered } = pipeline;
  const decision = policy?.decision ?? null;
  const escalated = decision === "escalate" || detail.status === "escalated";
  const stopped = decision === "stop" || detail.status === "stopped";

  // 1) DETECT — the case exists, so the risk signal was detected.
  const detect: JourneyStep = {
    key: "detect",
    label: "Detect",
    headline: workflowLabel(detail.case_type),
    lines: [
      `${money(detail.amount_at_risk, detail.currency)} at risk`,
      `Risk level: ${detail.risk_level.toUpperCase()}`,
    ],
    state: "done",
    tone: "neutral",
  };

  // 2) DIAGNOSE — advisory AI recommendation.
  const diagnose: JourneyStep = diagnosis
    ? {
        key: "diagnose",
        label: "Diagnose",
        headline: providerLabel(diagnosis.provider),
        lines: [
          `Recommends: ${diagnosis.action ? titleCase(diagnosis.action) : "—"}`,
          ...(confidencePct(diagnosis.confidence) ? [confidencePct(diagnosis.confidence)!] : []),
          ...(diagnosis.rationale ? [diagnosis.rationale] : []),
        ],
        state: "done",
        tone: "advisory",
      }
    : {
        key: "diagnose",
        label: "Diagnose",
        headline: "Awaiting AI diagnosis",
        lines: ["Run the agent to get a recommendation."],
        state: "pending",
        tone: "neutral",
      };

  // 3) POLICY — the deterministic authority.
  const policyStep: JourneyStep = policy
    ? {
        key: "policy",
        label: "Policy",
        headline: (decision ?? "—").toUpperCase(),
        lines: [
          ...(policy.matchedRules.length ? [`Rule: ${policy.matchedRules.join(", ")}`] : []),
          ...(policy.reason ? [policy.reason] : []),
        ],
        state: "done",
        tone: decision === "approve" ? "authority" : decision === "escalate" ? "warn" : "bad",
      }
    : {
        key: "policy",
        label: "Policy",
        headline: "Awaiting policy decision",
        lines: ["The PolicyEngine authorizes, escalates, or stops."],
        state: "pending",
        tone: "neutral",
      };

  // 4) EXECUTE — only ever runs on APPROVE.
  let execute: JourneyStep;
  if (escalated) {
    execute = {
      key: "execute",
      label: "Execute",
      headline: "No payment action executed",
      lines: ["Human review required before any action."],
      state: "skipped",
      tone: "warn",
    };
  } else if (stopped) {
    execute = {
      key: "execute",
      label: "Execute",
      headline: "No further execution",
      lines: ["Policy limit reached."],
      state: "blocked",
      tone: "bad",
    };
  } else if (execution) {
    const sim = isSimulatedExecution(execution);
    execute = {
      key: "execute",
      label: "Execute",
      headline: execution.action ? titleCase(execution.action) : "Bounded action",
      lines: [
        `Provider: ${executionProviderLabel(execution)}`,
        ...(execution.executionStatus ? [`Status: ${titleCase(execution.executionStatus)}`] : []),
        ...(execution.url
          ? [`${sim ? "Simulated link (demo)" : "Payment link"}: ${execution.url}`]
          : []),
        ...(execution.reference ? [`Ref: ${execution.reference}`] : []),
      ],
      state: "done",
      tone: "neutral",
    };
  } else {
    execute = {
      key: "execute",
      label: "Execute",
      headline: "Not executed yet",
      lines: ["Bounded action runs only after an APPROVE."],
      state: "pending",
      tone: "neutral",
    };
  }

  // 5) VERIFY — recovery is confirmed only here.
  let verify: JourneyStep;
  if (escalated || stopped) {
    verify = {
      key: "verify",
      label: "Verify",
      headline: "Nothing to verify",
      lines: ["No action was executed."],
      state: "skipped",
      tone: "neutral",
    };
  } else if (verifiedRecovered) {
    const sim = execution ? isSimulatedExecution(execution) : false;
    verify = {
      key: "verify",
      label: "Verify",
      headline: "Payment confirmed",
      lines: [
        `${money(detail.amount_recovered, detail.currency)} verified`,
        sim ? "Confirmed by simulated verification (demo)" : "Confirmed by Razorpay payment status",
      ],
      state: "done",
      tone: "good",
    };
  } else if (execution) {
    verify = {
      key: "verify",
      label: "Verify",
      headline: "Verifying provider status…",
      lines: ["A successful API call alone never counts as recovered."],
      state: "active",
      tone: "neutral",
    };
  } else {
    verify = {
      key: "verify",
      label: "Verify",
      headline: "Pending verification",
      lines: ["Recovery is confirmed only by verification."],
      state: "pending",
      tone: "neutral",
    };
  }

  // 6) OUTCOME — the recorded terminal state.
  let outcome: JourneyStep;
  if (detail.status === "recovered") {
    outcome = {
      key: "outcome",
      label: "Outcome",
      headline: "RECOVERED",
      lines: [`${money(detail.amount_recovered, detail.currency)} recovered (verified)`],
      state: "done",
      tone: "good",
    };
  } else if (detail.status === "escalated") {
    outcome = {
      key: "outcome",
      label: "Outcome",
      headline: "ESCALATED",
      lines: [detail.escalation_reason ?? "Handed to a human for review."],
      state: "done",
      tone: "warn",
    };
  } else if (detail.status === "stopped") {
    outcome = {
      key: "outcome",
      label: "Outcome",
      headline: "STOPPED",
      lines: [detail.stop_reason ? titleCase(detail.stop_reason) : "Stopped by policy."],
      state: "done",
      tone: "bad",
    };
  } else if (detail.status === "failed") {
    outcome = {
      key: "outcome",
      label: "Outcome",
      headline: "FAILED",
      lines: ["The workflow could not complete."],
      state: "done",
      tone: "bad",
    };
  } else if (detail.status === "waiting") {
    outcome = {
      key: "outcome",
      label: "Outcome",
      headline: "AWAITING PAYMENT",
      lines: ["Action executed — confirm the payment to complete recovery."],
      state: "active",
      tone: "neutral",
    };
  } else {
    outcome = {
      key: "outcome",
      label: "Outcome",
      headline: "In progress",
      lines: [titleCase(detail.status)],
      state: "active",
      tone: "neutral",
    };
  }

  return [detect, diagnose, policyStep, execute, verify, outcome];
}
