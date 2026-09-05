"""RevGuard policy layer (Stage 4) — the deterministic single approval gate.

Pure decision logic over domain objects: no LLM, Razorpay, FastAPI, database, or executor
dependency, and no side effects. :class:`PolicyEngine.evaluate` returns exactly one
:class:`~revguard.domain.PolicyDecision` (APPROVE / ESCALATE / STOP), fail-closed, with the
firing rule's id + reason for the audit log.
"""

from revguard.policy.context import (
    ActionRecord,
    PolicyConfig,
    PolicyContext,
    RuleId,
    RuleOutcome,
)
from revguard.policy.engine import PolicyEngine
from revguard.policy.permissions import (
    CONTACT_ACTIONS,
    WORKFLOW_PERMISSIONS,
    is_action_permitted,
    permitted_actions,
)
from revguard.policy.retry import (
    DEFAULT_RETRY_SCHEDULE_SECONDS,
    next_retry_at,
    retries_remaining,
    retry_delay_seconds,
)

__all__ = [
    "PolicyEngine",
    "PolicyConfig",
    "PolicyContext",
    "ActionRecord",
    "RuleId",
    "RuleOutcome",
    "WORKFLOW_PERMISSIONS",
    "CONTACT_ACTIONS",
    "permitted_actions",
    "is_action_permitted",
    "DEFAULT_RETRY_SCHEDULE_SECONDS",
    "retry_delay_seconds",
    "next_retry_at",
    "retries_remaining",
]
