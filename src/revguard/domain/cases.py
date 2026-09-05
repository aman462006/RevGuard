"""RecoveryCase + CaseStatus — the central business object (Stage 2 onward).

A ``RecoveryCase`` tracks a recovery workflow from detection to a terminal state. It is the
one **mutable** domain model (the orchestrator advances it), but ``validate_assignment`` and
consistency validators keep invalid/ambiguous states hard to represent. The full state
*transition* machine is deliberately NOT implemented here — that is Phase 4/7. This model
only defines the states and the shape of a case.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    MUTABLE_MODEL,
    Currency,
    NonNegativeMoney,
    PositiveMoney,
    WorkflowType,
    new_id,
    utcnow,
)
from revguard.domain.events import DataProvenance
from revguard.domain.promise import PromiseToPay
from revguard.domain.proposals import ActionType
from revguard.domain.signals import RevenueRiskSignal


class CaseStatus(StrEnum):
    """Lifecycle states of a recovery case.

    Non-terminal states describe work in progress; terminal states are final outcomes.
    """

    # Non-terminal
    DETECTED = "detected"
    ANALYZING = "analyzing"
    ACTION_PENDING = "action_pending"
    ACTION_APPROVED = "action_approved"
    ACTION_EXECUTING = "action_executing"
    WAITING = "waiting"

    # Terminal
    RECOVERED = "recovered"
    ESCALATED = "escalated"
    STOPPED = "stopped"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES: frozenset[CaseStatus] = frozenset(
    {
        CaseStatus.RECOVERED,
        CaseStatus.ESCALATED,
        CaseStatus.STOPPED,
        CaseStatus.FAILED,
    }
)


class StopReason(StrEnum):
    """Why a case stopped (set when status becomes STOPPED)."""

    MAX_ATTEMPTS_REACHED = "max_attempts_reached"
    CASE_EXPIRED = "case_expired"
    DO_NOT_CONTACT = "do_not_contact"
    ALREADY_RECOVERED = "already_recovered"
    DUPLICATE_EVENT = "duplicate_event"
    UNRECOVERABLE = "unrecoverable"
    POLICY_STOP = "policy_stop"
    MANUAL_STOP = "manual_stop"


class RecoveryCase(BaseModel):
    """A single recovery workflow instance."""

    model_config = MUTABLE_MODEL

    case_id: str = Field(default_factory=lambda: new_id("case"))
    case_type: WorkflowType

    customer_id: str | None = None

    # The signal that originated this case (composition — full traceability).
    signal: RevenueRiskSignal

    amount_at_risk: PositiveMoney
    currency: Currency
    amount_recovered: NonNegativeMoney = Decimal("0")

    status: CaseStatus = CaseStatus.DETECTED

    # Progress within the bounded recovery sequence.
    attempt_count: int = Field(default=0, ge=0)
    current_step: int = Field(default=0, ge=0)
    current_action: ActionType | None = None
    next_action: ActionType | None = None

    # Compliance / control state relevant to the policy engine.
    do_not_contact: bool = False

    # Promise-to-pay lifecycle (Workflow D). Set when a promise is recorded; advanced through
    # PROMISED → PENDING → KEPT/MISSED. A recorded promise is never recovery on its own.
    promise: PromiseToPay | None = None

    # Deterministic retry sequencing: the next eligible retry time, set by the orchestrator
    # from the PolicyEngine's fixed schedule when a failed-but-recoverable attempt is held for
    # a later retry. Persisted so the schedule survives restarts; ``None`` means no retry is
    # pending (either not yet attempted, awaiting verification, or terminal).
    next_retry_at: AwareDatetime | None = None

    # Lifecycle timestamps.
    created_at: AwareDatetime = Field(default_factory=utcnow)
    updated_at: AwareDatetime = Field(default_factory=utcnow)
    expires_at: AwareDatetime | None = None

    # Escalation info (populated when escalated).
    escalated_at: AwareDatetime | None = None
    escalation_reason: str | None = None

    # Stop info (populated when stopped).
    stopped_at: AwareDatetime | None = None
    stop_reason: StopReason | None = None

    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def provenance(self) -> DataProvenance:
        """Data origin of this case, inherited from its originating signal (for labelling)."""
        return self.signal.provenance

    @model_validator(mode="after")
    def _recovered_not_over_at_risk(self) -> RecoveryCase:
        if self.amount_recovered > self.amount_at_risk:
            raise ValueError("amount_recovered cannot exceed amount_at_risk")
        return self

    @model_validator(mode="after")
    def _terminal_state_consistency(self) -> RecoveryCase:
        """Light guards so terminal states are unambiguous (not a transition machine)."""
        if self.status is CaseStatus.RECOVERED and self.amount_recovered <= 0:
            raise ValueError("RECOVERED cases must have a positive amount_recovered")
        if self.status is CaseStatus.ESCALATED:
            if self.escalated_at is None or not self.escalation_reason:
                raise ValueError(
                    "ESCALATED cases must set escalated_at and escalation_reason"
                )
        if self.status is CaseStatus.STOPPED and self.stop_reason is None:
            raise ValueError("STOPPED cases must set stop_reason")
        return self


__all__ = ["RecoveryCase", "CaseStatus", "StopReason", "TERMINAL_STATUSES"]
