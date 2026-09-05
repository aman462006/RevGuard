"""Offline ground truth + verifier for reproducible evaluation (EVALUATION_SPEC §5).

In offline mode there is no live payment provider, so whether an action "recovers" money is
decided by a deterministic, seeded ground truth attached to each case — **not** by network
I/O. The ground truth is a property of the *case* (is this money recoverable, and how much
persistence does it take), identical for every strategy, so Baseline and RevGuard face the
same reality and any measured difference comes only from the strategies themselves.

:class:`GroundTruthVerifier` is a real :class:`~revguard.verification.OutcomeVerifier`: it
plugs into the existing orchestrator unchanged and remains the sole authority that may mark a
result RECOVERED. A recovery action recovers the money only once the case has received enough
attempts (``recover_at_attempt``); a ``WAIT`` (or any non-recovering action) stays PENDING; a
technically failed execution never becomes recovered.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from revguard.domain import (
    ActionProposal,
    ActionType,
    ExecutionStatus,
    RecoveryCase,
    RecoveryOutcome,
    RecoveryResult,
    VerificationStatus,
    utcnow,
)
from revguard.execution import ExecutionRecord
from revguard.verification import OutcomeVerifier

# Actions that can actually move money / a customer toward paying. WAIT and the
# recommendation actions are excluded (WAIT has no external effect; recommendations do not
# execute at all — the policy engine turns them into ESCALATE/STOP).
_RECOVERING_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECORD_PROMISE_TO_PAY,
    }
)

# How many distinct attempts a recoverable case may require before it recovers. A case that
# needs more attempts than a strategy is willing/able to make simply is not recovered by it.
_MAX_ATTEMPT_TIERS = 3


@dataclass(frozen=True)
class GroundTruth:
    """The seeded, strategy-independent truth about one case's recoverability."""

    case_id: str
    recoverable: bool
    recover_at_attempt: int  # 1.._MAX_ATTEMPT_TIERS (only meaningful when recoverable)
    recoverable_amount: Decimal


def _digest(seed: int, case_id: str) -> int:
    """A stable, cross-process integer derived from ``seed`` and ``case_id``.

    Uses SHA-256 rather than :func:`hash` because the built-in hash is randomised per process
    and would break reproducibility across runs.
    """
    raw = f"{seed}:{case_id}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def ground_truth_for(case: RecoveryCase, seed: int) -> GroundTruth:
    """Derive the deterministic ground truth for ``case`` under ``seed``.

    ~70% of cases are recoverable; a recoverable case recovers only once it has received
    ``recover_at_attempt`` attempts. The recoverable amount is the full amount at risk (the
    offline model recovers a case fully or not at all — no partial figures to double count).
    """
    h = _digest(seed, case.case_id)
    recoverable = (h % 10) < 7
    recover_at_attempt = (h // 10) % _MAX_ATTEMPT_TIERS + 1
    return GroundTruth(
        case_id=case.case_id,
        recoverable=recoverable,
        recover_at_attempt=recover_at_attempt,
        recoverable_amount=case.amount_at_risk,
    )


class GroundTruthVerifier(OutcomeVerifier):
    """Deterministic, offline verifier driven by each case's seeded ground truth."""

    name = "ground_truth"

    def __init__(self, *, seed: int) -> None:
        self._seed = seed

    def verify(
        self, case: RecoveryCase, proposal: ActionProposal, execution: ExecutionRecord
    ) -> RecoveryResult:
        action = execution.action
        now = utcnow()

        # A failed execution can never recover money.
        if execution.result.execution_status is ExecutionStatus.FAILED:
            return self._not_recovered(
                case, action, now, ExecutionStatus.FAILED, RecoveryOutcome.ACTION_FAILED
            )

        truth = ground_truth_for(case, self._seed)
        recovers = (
            action in _RECOVERING_ACTIONS
            and truth.recoverable
            and case.attempt_count >= truth.recover_at_attempt
        )

        if recovers:
            amount = min(truth.recoverable_amount, case.amount_at_risk)
            reference = execution.adapter_result.reference or f"ver_{execution.idempotency_key}"
            return RecoveryResult(
                case_id=case.case_id,
                action=action,
                execution_status=ExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.RECOVERED,
                outcome=RecoveryOutcome.RECOVERED,
                amount_recovered=amount,
                currency=case.currency,
                payment_reference=reference,
                verified_at=now,
                created_at=now,
            )

        # Non-recovering action (e.g. WAIT), or recoverable-but-not-yet: nothing confirmed.
        if action not in _RECOVERING_ACTIONS:
            return RecoveryResult(
                case_id=case.case_id,
                action=action,
                execution_status=ExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.PENDING,
                outcome=RecoveryOutcome.PENDING,
                amount_recovered=Decimal("0"),
                created_at=now,
            )
        return self._not_recovered(
            case, action, now, ExecutionStatus.SUCCEEDED, RecoveryOutcome.NOT_RECOVERED
        )

    @staticmethod
    def _not_recovered(
        case: RecoveryCase,
        action: ActionType,
        now,
        execution_status: ExecutionStatus,
        outcome: RecoveryOutcome,
    ) -> RecoveryResult:
        return RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=execution_status,
            verification_status=VerificationStatus.NOT_RECOVERED,
            outcome=outcome,
            amount_recovered=Decimal("0"),
            failure_reason=f"{action.value} did not recover the money",
            verified_at=now,
            created_at=now,
        )


__all__ = ["GroundTruth", "GroundTruthVerifier", "ground_truth_for"]
