"""Per-workflow action whitelist (POLICY_SPEC §4).

The engine holds an explicit allow-list of **executable** actions per workflow. An action
not on the list for a case's workflow can never be approved, regardless of what the AI
proposed. ``WAIT`` is permitted everywhere (it has no external side effect).
"""

from __future__ import annotations

from revguard.domain import (
    ActionProposal,
    ActionType,
    DecisionType,
    RecoveryCase,
    WorkflowType,
)
from revguard.policy.context import (
    PolicyConfig,
    PolicyContext,
    RuleId,
    RuleOutcome,
)

# Executable actions permitted per workflow. Escalate/stop are *decisions*, not actions,
# so they never appear here; the recommendation ActionTypes are handled before permissions.
WORKFLOW_PERMISSIONS: dict[WorkflowType, frozenset[ActionType]] = {
    # A — analytics/root-cause focused: no auto-executed recovery action; anything concrete
    # is escalated. WAIT (no side effect) is allowed.
    WorkflowType.PAYMENT_DEGRADATION: frozenset({ActionType.WAIT}),
    # B — failed subscription / mandate: retry, payment link, reminder.
    WorkflowType.FAILED_SUBSCRIPTION: frozenset(
        {
            ActionType.RETRY_PAYMENT,
            ActionType.CREATE_PAYMENT_LINK,
            ActionType.SEND_REMINDER,
            ActionType.WAIT,
        }
    ),
    # C — checkout abandonment: recovery contact / payment link.
    WorkflowType.CHECKOUT_ABANDONMENT: frozenset(
        {
            ActionType.CREATE_PAYMENT_LINK,
            ActionType.SEND_REMINDER,
            ActionType.WAIT,
        }
    ),
    # D — B2B overdue receivables: reminder, record promise-to-pay.
    WorkflowType.OVERDUE_RECEIVABLE: frozenset(
        {
            ActionType.SEND_REMINDER,
            ActionType.RECORD_PROMISE_TO_PAY,
            ActionType.WAIT,
        }
    ),
}

# Actions that reach out to the customer; blocked when do-not-contact is set.
CONTACT_ACTIONS: frozenset[ActionType] = frozenset(
    {ActionType.SEND_REMINDER, ActionType.CREATE_PAYMENT_LINK}
)


def permitted_actions(workflow: WorkflowType) -> frozenset[ActionType]:
    """Executable actions allowed for a workflow (empty set if unknown → fail closed)."""
    return WORKFLOW_PERMISSIONS.get(workflow, frozenset())


def is_action_permitted(workflow: WorkflowType, action: ActionType) -> bool:
    return action in permitted_actions(workflow)


def check_permission(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Escalate any executable action not whitelisted for the case's workflow."""
    if is_action_permitted(case.case_type, proposal.action_type):
        return None
    return RuleOutcome(
        decision=DecisionType.ESCALATE,
        rule_id=RuleId.ESCALATE_PERMISSION_DENIED,
        reason=(
            f"action {proposal.action_type.value!r} is not permitted for workflow "
            f"{case.case_type.value!r}; escalating for human review"
        ),
    )


__all__ = [
    "WORKFLOW_PERMISSIONS",
    "CONTACT_ACTIONS",
    "permitted_actions",
    "is_action_permitted",
    "check_permission",
]
