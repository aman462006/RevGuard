"""PolicyDecision — the deterministic gate's output (Stage 4).

This object represents **deterministic application logic**, not AI output. It is produced
by the policy engine (Phase 4) and is the *only* thing that can authorize execution. The AI
/ diagnosis layer must never import or construct a ``PolicyDecision`` — it deals only in
:class:`~revguard.domain.proposals.ActionProposal`. Decisions are immutable.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    FROZEN_MODEL,
    NonNegativeMoney,
    new_id,
    utcnow,
)
from revguard.domain.proposals import EXECUTABLE_ACTIONS, ActionType


class DecisionType(StrEnum):
    """The only three outcomes the policy engine may produce."""

    APPROVE = "approve"
    ESCALATE = "escalate"
    STOP = "stop"


class PolicyDecision(BaseModel):
    """A deterministic authorization decision for a proposed action on a case."""

    model_config = FROZEN_MODEL

    decision_id: str = Field(default_factory=lambda: new_id("dec"))
    case_id: str

    decision: DecisionType
    # The action under consideration. Required for APPROVE (the sanctioned action);
    # optional for ESCALATE / STOP.
    proposed_action: ActionType | None = None

    reason: str = Field(min_length=1)
    # Identifiers of the policy rule(s) that produced this decision — every decision is
    # traceable to at least one rule.
    matched_rules: list[str] = Field(min_length=1)

    # Relevant counters / limits captured at decision time (for audit + dashboards).
    attempt_count: int | None = Field(default=None, ge=0)
    max_attempts: int | None = Field(default=None, ge=0)
    amount_at_risk: NonNegativeMoney | None = None
    threshold_amount: NonNegativeMoney | None = None

    decided_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _approve_requires_executable_action(self) -> PolicyDecision:
        if self.decision is DecisionType.APPROVE:
            if self.proposed_action is None:
                raise ValueError("APPROVE decisions must reference a proposed_action")
            if self.proposed_action not in EXECUTABLE_ACTIONS:
                raise ValueError(
                    "APPROVE may only sanction an executable action; "
                    f"{self.proposed_action.value!r} is not executable"
                )
        return self


__all__ = ["DecisionType", "PolicyDecision"]
