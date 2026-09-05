"""ActionType + ActionProposal — the AI's output contract (Stage 3).

AI SAFETY BOUNDARY
==================
An ``ActionProposal`` is the *only* thing the AI layer may emit. It describes a **permitted
business action from a finite whitelist** — nothing more. The model makes it impossible for
the AI to represent an arbitrary external operation:

* ``action_type`` is a closed enum. There is no ``execute_tool`` / generic action.
* There are no fields for URLs, endpoints, function/tool names, SQL, code, or credentials.
* ``parameters`` is a scalar-only mapping (no nested objects/callables), interpreted by the
  deterministic executor per action type in a later phase.

A proposal is a **recommendation, not an authorization.** It has no ``approved`` field and
carries no authority. Only the deterministic policy engine may authorize execution, via a
separate :class:`~revguard.domain.decisions.PolicyDecision`. Recommending escalation/stop
is itself expressed as a *recommendation* (``RECOMMEND_ESCALATION`` / ``RECOMMEND_STOP``),
distinct from the binding ``ESCALATE`` / ``STOP`` policy decisions.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    FROZEN_MODEL,
    Confidence,
    Currency,
    PositiveMoney,
    new_id,
    utcnow,
)

# Scalar values only — deliberately no dict/list/object, so parameters can never smuggle a
# nested "operation". None is allowed for optional/absent values.
ParameterValue = str | int | float | bool | None


class ActionType(StrEnum):
    """Finite whitelist of business actions the AI may recommend.

    Mirrors the actions the executor + policy engine understand (AGENT_SPEC §5). The
    ``RECOMMEND_*`` members are recommendations only; the binding escalate/stop lives in
    :class:`~revguard.domain.decisions.DecisionType`.
    """

    # Executable recovery actions.
    RETRY_PAYMENT = "retry_payment"
    CREATE_PAYMENT_LINK = "create_payment_link"
    SEND_REMINDER = "send_reminder"
    RECORD_PROMISE_TO_PAY = "record_promise_to_pay"
    WAIT = "wait"

    # Non-executing recommendations / no-ops.
    NO_ACTION = "no_action"
    RECOMMEND_ESCALATION = "recommend_escalation"
    RECOMMEND_STOP = "recommend_stop"


# Actions that actually cause a side effect when executed (used by the policy engine to
# validate what an APPROVE may sanction). WAIT is executable (it schedules a delay) but
# causes no external side effect.
EXECUTABLE_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECORD_PROMISE_TO_PAY,
        ActionType.WAIT,
    }
)

# Actions that are pure recommendations and never executed directly.
RECOMMENDATION_ACTIONS: frozenset[ActionType] = frozenset(
    {
        ActionType.RECOMMEND_ESCALATION,
        ActionType.RECOMMEND_STOP,
        ActionType.NO_ACTION,
    }
)


class ActionProposal(BaseModel):
    """What the AI recommends for a case. Immutable; carries no execution authority."""

    model_config = FROZEN_MODEL

    proposal_id: str = Field(default_factory=lambda: new_id("prop"))
    case_id: str

    action_type: ActionType
    rationale: str = Field(min_length=1)
    confidence: Confidence

    # Scalar-only, action-specific parameters (validated per action type in a later phase).
    parameters: dict[str, ParameterValue] = Field(default_factory=dict)

    # Evidence the AI relied on (read-only context echoes / references).
    evidence: dict[str, ParameterValue] = Field(default_factory=dict)

    # Expected recovery, where the AI can estimate it. Travels with a currency.
    expected_recovery_amount: PositiveMoney | None = None
    expected_recovery_currency: Currency | None = None

    # Optional timing hints. The policy engine and orchestrator remain the authorities on
    # when (or whether) an action actually runs.
    suggested_delay_seconds: int | None = Field(default=None, ge=0)
    not_before: AwareDatetime | None = None

    proposed_at: AwareDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _expected_amount_currency_paired(self) -> ActionProposal:
        if (self.expected_recovery_amount is None) != (
            self.expected_recovery_currency is None
        ):
            raise ValueError(
                "expected_recovery_amount and expected_recovery_currency must be "
                "provided together"
            )
        return self


__all__ = [
    "ActionType",
    "ActionProposal",
    "EXECUTABLE_ACTIONS",
    "RECOMMENDATION_ACTIONS",
    "ParameterValue",
]
