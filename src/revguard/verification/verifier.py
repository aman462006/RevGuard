"""OutcomeVerifier — the sole authority on whether money was actually recovered (Stage 6/7).

A technically successful execution is **not** recovery. The executor reports only that an
action ran; this layer independently determines the *verified* outcome and is the **only**
place a :class:`RecoveryResult` may be marked ``RECOVERED`` with a recovered amount.

:class:`MockVerifier` is a deterministic, offline stand-in that can simulate every outcome
the pipeline must handle — recovered, still pending, failed, and not-recoverable — without
touching a real payment provider. Real provider-backed verification arrives in a later phase
behind the same :class:`OutcomeVerifier` interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

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


class OutcomeVerifier(ABC):
    """Verifies the real-world outcome of an executed action.

    Implementations translate a technical :class:`ExecutionRecord` into a verified
    :class:`RecoveryResult`. Only a verifier may declare ``RECOVERED`` + an amount.
    """

    name: str = "verifier"

    @abstractmethod
    def verify(
        self, case: RecoveryCase, proposal: ActionProposal, execution: ExecutionRecord
    ) -> RecoveryResult: ...


class MockVerifier(OutcomeVerifier):
    """Deterministic verifier. Produces a fixed or scripted verification status.

    ``default`` is returned for every call unless ``sequence`` is given, in which case each
    call consumes the next status (clamped to the last). A technically **failed** execution
    always verifies as not-recovered — money can never come from an action that did not run.
    A ``WAIT`` action is inherently "still pending" until a later check.
    """

    name = "mock"

    def __init__(
        self,
        *,
        default: VerificationStatus = VerificationStatus.NOT_RECOVERED,
        sequence: Sequence[VerificationStatus] | None = None,
        recovered_amount: Decimal | None = None,
        clock: Callable[[], Any] = utcnow,
    ) -> None:
        self._default = default
        self._sequence = list(sequence) if sequence else None
        self._recovered_amount = recovered_amount
        self._clock = clock
        self._i = 0

    def _next_status(self) -> VerificationStatus:
        if self._sequence:
            status = self._sequence[min(self._i, len(self._sequence) - 1)]
            self._i += 1
            return status
        return self._default

    def verify(
        self, case: RecoveryCase, proposal: ActionProposal, execution: ExecutionRecord
    ) -> RecoveryResult:
        action = execution.action

        # A failed action recovers nothing — never let it become RECOVERED.
        if execution.result.execution_status is ExecutionStatus.FAILED:
            return self._not_recovered(
                case,
                action,
                execution_status=ExecutionStatus.FAILED,
                outcome=RecoveryOutcome.ACTION_FAILED,
                failure_reason=execution.result.failure_reason or f"{action.value} failed",
            )

        status = self._next_status()

        # WAIT has nothing to confirm yet unless explicitly scripted as recovered.
        if action is ActionType.WAIT and status is not VerificationStatus.RECOVERED:
            status = VerificationStatus.PENDING

        if status is VerificationStatus.RECOVERED:
            return self._recovered(case, proposal, action, execution)
        if status is VerificationStatus.PENDING:
            return self._pending(case, action)
        # NOT_RECOVERED / UNVERIFIED — action ran but money was not recovered.
        return self._not_recovered(
            case,
            action,
            execution_status=ExecutionStatus.SUCCEEDED,
            outcome=RecoveryOutcome.NOT_RECOVERED,
            failure_reason="verified not recovered",
        )

    # -- result builders ----------------------------------------------------------------

    def _recovered(
        self,
        case: RecoveryCase,
        proposal: ActionProposal,
        action: ActionType,
        execution: ExecutionRecord,
    ) -> RecoveryResult:
        amount = self._recovered_amount or proposal.expected_recovery_amount or case.amount_at_risk
        amount = min(Decimal(amount), case.amount_at_risk)  # never exceed amount at risk
        reference = execution.adapter_result.reference or f"ver_{execution.idempotency_key}"
        now = self._clock()
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

    def _pending(self, case: RecoveryCase, action: ActionType) -> RecoveryResult:
        return RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.PENDING,
            outcome=RecoveryOutcome.PENDING,
            amount_recovered=Decimal("0"),
            created_at=self._clock(),
        )

    def _not_recovered(
        self,
        case: RecoveryCase,
        action: ActionType,
        *,
        execution_status: ExecutionStatus,
        outcome: RecoveryOutcome,
        failure_reason: str,
    ) -> RecoveryResult:
        now = self._clock()
        return RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=execution_status,
            verification_status=VerificationStatus.NOT_RECOVERED,
            outcome=outcome,
            amount_recovered=Decimal("0"),
            failure_reason=failure_reason,
            verified_at=now,
            created_at=now,
        )


__all__ = ["OutcomeVerifier", "MockVerifier"]
