"""RevGuard orchestrator (Stage 7) — the deterministic end-to-end recovery control loop.

:class:`RecoveryAgent` sequences detection → diagnosis → policy → execution → verification
for each case, enforcing explicit state transitions, recording the audit trail, and stopping
on a terminal decision or the step budget. It owns no business rules: the diagnoser proposes,
the policy engine decides, the executor acts only on APPROVE, and only the verifier declares
recovery.
"""

from revguard.orchestrator.recovery_agent import (
    HumanReviewError,
    InvalidTransition,
    ReconciliationResult,
    RecoveryAgent,
    RecoveryConfirmation,
)

__all__ = [
    "RecoveryAgent",
    "InvalidTransition",
    "HumanReviewError",
    "RecoveryConfirmation",
    "ReconciliationResult",
]
