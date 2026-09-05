"""Prompt construction and the schema-constrained output tool (AGENT_SPEC §5, §6).

Builds a concise, read-only case briefing (no secrets, minimal customer reference) and the
system prompt that constrains the model to the RevGuard task. Output is obtained via a
schema-constrained *tool* (``submit_action_proposal``) so the model returns a structured
object — never free-form text parsed with fragile string matching.

This module imports only domain models (no policy/execution/DB), keeping the AI layer
independent. The per-workflow action *hints* below are prompt guidance only; the
deterministic policy engine remains the sole authority on what may actually run.
"""

from __future__ import annotations

import json
from typing import Any

from revguard.domain import ActionType, Currency, RecoveryCase, WorkflowType

# Every action the schema allows the model to choose (the full enum → "never invent
# actions"). The model may only pick one of these strings.
ALLOWED_ACTION_VALUES: list[str] = [a.value for a in ActionType]

# Prompt-only guidance on which concrete actions typically fit each workflow. NOT the
# authoritative permission list (that lives in the policy engine).
WORKFLOW_ACTION_HINTS: dict[WorkflowType, list[ActionType]] = {
    WorkflowType.PAYMENT_DEGRADATION: [
        ActionType.WAIT,
        ActionType.RECOMMEND_ESCALATION,
    ],
    WorkflowType.FAILED_SUBSCRIPTION: [
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECOMMEND_ESCALATION,
    ],
    WorkflowType.CHECKOUT_ABANDONMENT: [
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECOMMEND_STOP,
    ],
    WorkflowType.OVERDUE_RECEIVABLE: [
        ActionType.SEND_REMINDER,
        ActionType.RECORD_PROMISE_TO_PAY,
        ActionType.RECOMMEND_ESCALATION,
    ],
}

SUBMIT_TOOL_NAME = "submit_action_proposal"

SYSTEM_PROMPT = f"""\
You are RevGuard's revenue-recovery diagnosis assistant.

Your ONLY job is to recommend one recovery action for the given case by calling the \
`{SUBMIT_TOOL_NAME}` tool. You are a reasoning/recommendation component — you do not and \
cannot execute anything.

Rules you MUST follow:
- Return ONLY a structured proposal via the `{SUBMIT_TOOL_NAME}` tool. No free-form answer.
- Choose `action_type` strictly from the allowed enum. Never invent an action.
- Never execute an action, and never claim an action was performed. You only recommend.
- Base your reasoning solely on the supplied evidence. Do not fabricate facts.
- If you are uncertain, prefer WAIT or RECOMMEND_ESCALATION.
- Do not attempt to override or reason about policy limits — a separate deterministic \
policy engine decides whether your recommendation is approved, escalated, or stopped.

Allowed action_type values: {", ".join(ALLOWED_ACTION_VALUES)}.
"""


def _safe_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Keep only JSON-serialisable scalar evidence; drop anything unexpected."""
    safe: dict[str, Any] = {}
    for key, value in evidence.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
    return safe


def _compact_root_cause(root_cause: Any) -> dict[str, Any] | None:
    """A bounded, scalar-only view of the degradation root-cause breakdown for the model.

    Only the per-dimension dominant segment + share + availability are forwarded (not the full
    segment map) so the prompt stays small and the model reasons on the concentrated driver.
    Unavailable dimensions are passed through as ``{"available": false}`` — never invented.
    """
    if not isinstance(root_cause, dict):
        return None
    dims = root_cause.get("dimensions")
    if not isinstance(dims, dict):
        return None
    out: dict[str, Any] = {"primary": root_cause.get("primary")}
    for name, d in dims.items():
        if not isinstance(d, dict):
            continue
        if not d.get("available"):
            out[name] = {"available": False}
        else:
            out[name] = {
                "available": True,
                "dominant": d.get("dominant"),
                "dominant_failures": d.get("dominant_failures"),
                "dominant_share": d.get("dominant_share"),
                "coverage": d.get("coverage"),
            }
    return out


def build_case_briefing(case: RecoveryCase) -> dict[str, Any]:
    """A minimal, read-only view of the case for the model (no secrets/PII beyond ref)."""
    signal = case.signal
    hints = WORKFLOW_ACTION_HINTS.get(case.case_type, [])
    briefing: dict[str, Any] = {
        "workflow": case.case_type.value,
        "customer_ref": case.customer_id,
        "risk_level": signal.risk_level.value,
        "amount_at_risk": str(case.amount_at_risk),
        "currency": case.currency.value,
        "attempt_count": case.attempt_count,
        "do_not_contact": case.do_not_contact,
        "evidence": _safe_evidence(signal.evidence),
        "suggested_actions": [a.value for a in hints],
    }
    root_cause = _compact_root_cause(signal.evidence.get("root_cause"))
    if root_cause is not None:
        briefing["root_cause_breakdown"] = root_cause
    return briefing


def build_user_prompt(case: RecoveryCase) -> str:
    briefing = build_case_briefing(case)
    return (
        "Diagnose this revenue-at-risk case and recommend one action.\n\n"
        f"Case briefing:\n{json.dumps(briefing, indent=2, sort_keys=True)}\n\n"
        f"Call `{SUBMIT_TOOL_NAME}` with your single recommendation."
    )


def proposal_tool_schema() -> dict[str, Any]:
    """Anthropic tool definition giving schema-constrained ActionProposal-draft output."""
    return {
        "name": SUBMIT_TOOL_NAME,
        "description": (
            "Submit exactly one recommended recovery action for the case. "
            "This is a recommendation only; it does not execute anything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ALLOWED_ACTION_VALUES,
                    "description": "The single recommended action, from the allowed enum.",
                },
                "rationale": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Concise reason grounded in the supplied evidence.",
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional scalar parameters for the action.",
                },
                "expected_recovery_amount": {
                    "type": ["string", "null"],
                    "description": "Optional expected recovery amount as a decimal string.",
                },
                "expected_recovery_currency": {
                    "type": ["string", "null"],
                    "enum": [*[c.value for c in Currency], None],
                },
            },
            "required": ["action_type", "rationale", "confidence"],
            "additionalProperties": False,
        },
    }


__all__ = [
    "ALLOWED_ACTION_VALUES",
    "WORKFLOW_ACTION_HINTS",
    "SUBMIT_TOOL_NAME",
    "SYSTEM_PROMPT",
    "build_case_briefing",
    "build_user_prompt",
    "proposal_tool_schema",
]
