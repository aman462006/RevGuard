"""RecoveryResult — the verified outcome of an attempted recovery action (Stage 6/7).

CORE RULE
=========
A payment is **not** considered recovered merely because an action executed successfully.
This model separates three independent facts:

1. ``execution_status``   — did the action run *technically*? (executor concern)
2. ``verification_status``— was money *actually* recovered? (verification concern)
3. ``amount_recovered``   — how much was verified as recovered.

Money recovered is driven **only** by ``verification_status == RECOVERED`` — never inferred
from a successful execution. Results are immutable.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    FROZEN_MODEL,
    Currency,
    NonNegativeMoney,
    new_id,
    utcnow,
)
from revguard.domain.proposals import ActionType


class ExecutionStatus(StrEnum):
    """Whether the action ran, technically. Says nothing about money recovered."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REJECTED = "rejected"  # e.g. executor refused a non-APPROVE action


class VerificationStatus(StrEnum):
    """Whether money was actually recovered, per verification."""

    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"
    PENDING = "pending"
    UNVERIFIED = "unverified"


class RecoveryOutcome(StrEnum):
    """Overall, human-readable outcome of a recovery attempt."""

    RECOVERED = "recovered"
    PARTIALLY_RECOVERED = "partially_recovered"
    NOT_RECOVERED = "not_recovered"
    PENDING = "pending"
    ACTION_FAILED = "action_failed"
    NO_ACTION = "no_action"


class RecoveryResult(BaseModel):
    """The verified result of a single recovery attempt on a case."""

    model_config = FROZEN_MODEL

    result_id: str = Field(default_factory=lambda: new_id("res"))
    case_id: str

    # The action attempted (None when no action was taken).
    action: ActionType | None = None

    execution_status: ExecutionStatus
    verification_status: VerificationStatus
    outcome: RecoveryOutcome

    amount_recovered: NonNegativeMoney = Decimal("0")
    currency: Currency | None = None

    # Provider/payment reference proving the recovery, where applicable.
    payment_reference: str | None = None

    failure_reason: str | None = None

    verified_at: AwareDatetime | None = None
    created_at: AwareDatetime = Field(default_factory=utcnow)

    # -- Derived, authoritative view of "was money recovered?" --------------------------

    @property
    def is_recovered(self) -> bool:
        """True only when verification confirms recovery — not from execution success."""
        return self.verification_status is VerificationStatus.RECOVERED

    @property
    def action_succeeded_technically(self) -> bool:
        """Whether the action ran successfully, independent of recovery."""
        return self.execution_status is ExecutionStatus.SUCCEEDED

    # -- Consistency validators ---------------------------------------------------------

    @model_validator(mode="after")
    def _recovery_requires_verification(self) -> RecoveryResult:
        recovered = self.verification_status is VerificationStatus.RECOVERED
        if not recovered and self.amount_recovered > 0:
            raise ValueError(
                "amount_recovered must be 0 unless verification_status is RECOVERED "
                "(a technically successful action does not mean money was recovered)"
            )
        if recovered:
            if self.amount_recovered <= 0:
                raise ValueError("RECOVERED results must have a positive amount_recovered")
            if self.payment_reference is None:
                raise ValueError("RECOVERED results must include a payment_reference")
        return self

    @model_validator(mode="after")
    def _currency_present_when_money(self) -> RecoveryResult:
        if self.amount_recovered > 0 and self.currency is None:
            raise ValueError("currency is required when amount_recovered > 0")
        return self

    @model_validator(mode="after")
    def _failure_reason_when_failed(self) -> RecoveryResult:
        if self.execution_status is ExecutionStatus.FAILED and not self.failure_reason:
            raise ValueError("failure_reason is required when execution_status is FAILED")
        return self

    @model_validator(mode="after")
    def _outcome_consistency(self) -> RecoveryResult:
        o = self.outcome
        if o in (RecoveryOutcome.RECOVERED, RecoveryOutcome.PARTIALLY_RECOVERED):
            if self.verification_status is not VerificationStatus.RECOVERED:
                raise ValueError(f"outcome {o.value!r} requires verification RECOVERED")
        elif o is RecoveryOutcome.NOT_RECOVERED:
            if self.verification_status is not VerificationStatus.NOT_RECOVERED:
                raise ValueError("outcome NOT_RECOVERED requires verification NOT_RECOVERED")
        elif o is RecoveryOutcome.PENDING:
            if self.verification_status is not VerificationStatus.PENDING:
                raise ValueError("outcome PENDING requires verification PENDING")
        elif o is RecoveryOutcome.ACTION_FAILED:
            if self.execution_status not in (
                ExecutionStatus.FAILED,
                ExecutionStatus.REJECTED,
            ):
                raise ValueError("outcome ACTION_FAILED requires execution FAILED/REJECTED")
        elif o is RecoveryOutcome.NO_ACTION:
            if self.action is not None:
                raise ValueError("outcome NO_ACTION requires action to be None")
        return self


__all__ = [
    "RecoveryResult",
    "ExecutionStatus",
    "VerificationStatus",
    "RecoveryOutcome",
]
