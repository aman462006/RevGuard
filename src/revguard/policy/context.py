"""Shared inputs and primitives for the deterministic policy engine.

Everything the engine needs is passed in explicitly — there are **no** hidden lookups to a
database, network, or clock. :class:`PolicyContext` carries the case history the engine
reasons about; :class:`PolicyConfig` holds the (deterministic) thresholds. Rule functions
return a :class:`RuleOutcome`, and :class:`RuleId` gives every rule a stable identifier so a
:class:`~revguard.domain.PolicyDecision` can later explain itself in the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from revguard.domain import ActionType, DecisionType, StopReason, utcnow
from revguard.policy.retry import DEFAULT_RETRY_SCHEDULE_SECONDS


class RuleId(StrEnum):
    """Stable identifiers for every policy rule (recorded in ``matched_rules``)."""

    # Fail-closed precondition
    ESCALATE_CONTEXT_MISMATCH = "escalate.context_mismatch"
    ESCALATE_MISSING_CONTEXT = "escalate.missing_context"

    # Hard stops (terminal)
    STOP_ALREADY_RECOVERED = "stop.already_recovered"
    STOP_TERMINAL_CASE = "stop.terminal_case"
    STOP_CASE_EXPIRED = "stop.case_expired"
    STOP_MAX_ATTEMPTS = "stop.max_attempts_reached"
    STOP_DO_NOT_CONTACT = "stop.do_not_contact"
    STOP_DUPLICATE_ACTION = "stop.duplicate_action"

    # AI recommendation intents
    STOP_AI_RECOMMENDED_STOP = "stop.ai_recommended_stop"
    ESCALATE_AI_RECOMMENDED_ESCALATION = "escalate.ai_recommended_escalation"
    ESCALATE_NO_ACTION_REVIEW = "escalate.no_action_review"

    # Permission whitelist
    ESCALATE_PERMISSION_DENIED = "escalate.permission_denied"

    # Promise-to-pay lifecycle
    ESCALATE_DUPLICATE_PROMISE = "escalate.duplicate_promise"

    # Escalation thresholds
    ESCALATE_AMOUNT_THRESHOLD = "escalate.amount_over_threshold"
    ESCALATE_LOW_CONFIDENCE_HIGH_VALUE = "escalate.low_confidence_high_value"

    # Transient guardrail
    STOP_COOLDOWN_ACTIVE = "stop.cooldown_active"

    # Approval
    APPROVE_PERMITTED_ACTION = "approve.permitted_action"


@dataclass(frozen=True)
class RuleOutcome:
    """The result of a single rule firing."""

    decision: DecisionType
    rule_id: RuleId
    reason: str
    stop_reason: StopReason | None = None
    threshold_amount: Decimal | None = None


@dataclass(frozen=True)
class ActionRecord:
    """A previously executed action on the case (for cooldown/idempotency reasoning)."""

    action_type: ActionType
    occurred_at: datetime
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PolicyConfig:
    """Deterministic thresholds. Amounts are in INR major units (primary demo currency)."""

    max_attempts: int = 4
    cooldown_seconds: int = 3600
    escalation_amount_threshold: Decimal = Decimal("50000")
    high_value_amount: Decimal = Decimal("25000")
    min_confidence: float = 0.5
    # Deterministic retry cadence: delay (seconds) before attempts 1..N (attempt 1 immediate,
    # then +30m, +6h, +24h). Its length also bounds retries in step with ``max_attempts``.
    retry_schedule_seconds: tuple[int, ...] = DEFAULT_RETRY_SCHEDULE_SECONDS


@dataclass(frozen=True)
class PolicyContext:
    """Case history/state the engine needs. All fields are caller-supplied.

    ``now`` may be ``None`` to model "current time unavailable"; rules that need it then
    fail closed (ESCALATE) rather than guessing. It defaults to the current UTC time.
    """

    now: datetime | None = field(default_factory=utcnow)
    action_history: tuple[ActionRecord, ...] = ()
    executed_idempotency_keys: frozenset[str] = frozenset()


__all__ = [
    "RuleId",
    "RuleOutcome",
    "ActionRecord",
    "PolicyConfig",
    "PolicyContext",
]
