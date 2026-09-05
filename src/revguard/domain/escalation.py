"""EscalationRecord — a persistent human-escalation queue entry (PRODUCT_SPEC §6).

Created when the policy engine returns ESCALATE. Contains everything a human needs to act,
and supports being marked resolved. Included here because ARCHITECTURE.md lists
``domain/escalation.py`` as a domain contract; no external requirement is invented.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    MUTABLE_MODEL,
    Currency,
    PositiveMoney,
    new_id,
    utcnow,
)
from revguard.domain.decisions import DecisionType
from revguard.domain.proposals import ActionType


class EscalationStatus(StrEnum):
    """Lifecycle of a human-escalation record."""

    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class EscalationRecord(BaseModel):
    """A human-actionable escalation. Mutable so a human can move it to RESOLVED."""

    model_config = MUTABLE_MODEL

    escalation_id: str = Field(default_factory=lambda: new_id("esc"))
    case_id: str

    reason: str = Field(min_length=1)
    amount_at_risk: PositiveMoney
    currency: Currency
    customer_id: str | None = None

    # What the AI recommended, and the binding policy decision that caused escalation.
    ai_recommended_action: ActionType | None = None
    ai_rationale: str | None = None
    policy_decision: DecisionType = DecisionType.ESCALATE

    status: EscalationStatus = EscalationStatus.OPEN
    escalated_at: AwareDatetime = Field(default_factory=utcnow)

    resolved_at: AwareDatetime | None = None
    resolution_note: str | None = None

    @model_validator(mode="after")
    def _policy_decision_is_escalate(self) -> EscalationRecord:
        if self.policy_decision is not DecisionType.ESCALATE:
            raise ValueError("EscalationRecord.policy_decision must be ESCALATE")
        return self

    @model_validator(mode="after")
    def _resolved_requires_timestamp(self) -> EscalationRecord:
        if self.status is EscalationStatus.RESOLVED and self.resolved_at is None:
            raise ValueError("RESOLVED escalations must set resolved_at")
        return self


__all__ = ["EscalationRecord", "EscalationStatus"]
