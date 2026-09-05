"""Baseline strategy: a deterministic, rule-only recovery diagnoser (EVALUATION_SPEC §2).

The baseline is the "what a fixed playbook does" control for the comparison. It uses **no
LLM** and no case history: each workflow maps to a single, fixed first action. Because it
never varies its action, the policy engine's cooldown naturally prevents it from hammering
the same action, so the baseline effectively gets one shot per case — exactly the behaviour a
naive rule-only recovery loop exhibits.

It is a normal :class:`~revguard.diagnosis.Diagnoser`, so the baseline run reuses the entire
existing pipeline (detector → policy → executor → verifier) unchanged; only the diagnosis
strategy differs from RevGuard.
"""

from __future__ import annotations

from revguard.diagnosis import Diagnoser
from revguard.domain import ActionProposal, ActionType, RecoveryCase, WorkflowType

# One fixed action per workflow. Every action here is on that workflow's permission whitelist
# (see policy/permissions.py), so the baseline is never escalated purely for choosing an
# action the workflow does not allow.
_BASELINE_ACTION: dict[WorkflowType, ActionType] = {
    WorkflowType.PAYMENT_DEGRADATION: ActionType.WAIT,
    WorkflowType.FAILED_SUBSCRIPTION: ActionType.RETRY_PAYMENT,
    WorkflowType.CHECKOUT_ABANDONMENT: ActionType.CREATE_PAYMENT_LINK,
    WorkflowType.OVERDUE_RECEIVABLE: ActionType.SEND_REMINDER,
}


class RuleOnlyDiagnoser(Diagnoser):
    """Fixed per-workflow action with no AI and no contextual adaptation."""

    name = "baseline_rule_only"

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        action = _BASELINE_ACTION.get(case.case_type, ActionType.RECOMMEND_ESCALATION)
        return ActionProposal(
            case_id=case.case_id,
            action_type=action,
            rationale=f"[baseline] fixed rule for workflow {case.case_type.value}: {action.value}",
            confidence=0.7,  # fixed; policy—not confidence—decides whether it runs
            evidence={"strategy": self.name},
        )


__all__ = ["RuleOnlyDiagnoser"]
