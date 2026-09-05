"""Guardrails: consistency, idempotency, thresholds, and cooldown (POLICY_SPEC §3).

These deterministic checks sit between "is this action permitted" and "approve". They fail
closed: a duplicate action or an active cooldown is never approved, high amounts / low
confidence escalate to a human, and an incoherent proposal escalates rather than executes.
"""

from __future__ import annotations

from revguard.domain import ActionProposal, DecisionType, RecoveryCase, StopReason
from revguard.policy.context import (
    PolicyConfig,
    PolicyContext,
    RuleId,
    RuleOutcome,
)


def check_case_consistency(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Fail closed if the proposal does not clearly belong to this case."""
    if proposal.case_id != case.case_id:
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_CONTEXT_MISMATCH,
            reason=(
                f"proposal.case_id {proposal.case_id!r} does not match case "
                f"{case.case_id!r}; cannot evaluate safely"
            ),
        )
    return None


def _idempotency_key(proposal: ActionProposal, case: RecoveryCase) -> str:
    key = proposal.parameters.get("idempotency_key")
    if key is not None:
        return str(key)
    # Deterministic fallback: one action of a type per attempt.
    return f"{case.case_id}:{proposal.action_type.value}:{case.attempt_count}"


def check_duplicate_action(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Never re-execute an action whose idempotency key was already used."""
    key = _idempotency_key(proposal, case)
    if key in context.executed_idempotency_keys:
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_DUPLICATE_ACTION,
            reason=f"idempotency key {key!r} was already executed; not repeating",
            stop_reason=StopReason.DUPLICATE_EVENT,
        )
    return None


def check_amount_threshold(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Amounts above the escalation threshold require a human, not auto-approval."""
    if case.amount_at_risk > config.escalation_amount_threshold:
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_AMOUNT_THRESHOLD,
            reason=(
                f"amount_at_risk {case.amount_at_risk} exceeds escalation threshold "
                f"{config.escalation_amount_threshold}"
            ),
            threshold_amount=config.escalation_amount_threshold,
        )
    return None


def check_low_confidence_high_value(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Low AI confidence on a high-value case escalates (confidence never approves)."""
    if (
        proposal.confidence < config.min_confidence
        and case.amount_at_risk >= config.high_value_amount
    ):
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_LOW_CONFIDENCE_HIGH_VALUE,
            reason=(
                f"low AI confidence {proposal.confidence} on high-value case "
                f"(amount_at_risk {case.amount_at_risk}); escalating"
            ),
            threshold_amount=config.high_value_amount,
        )
    return None


def check_cooldown(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Do not repeat the same action before its cooldown window elapses."""
    same = [
        r for r in context.action_history if r.action_type == proposal.action_type
    ]
    if not same:
        return None
    if context.now is None:
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_MISSING_CONTEXT,
            reason="cannot evaluate cooldown without a current time; failing closed",
        )
    last = max(r.occurred_at for r in same)
    elapsed = (context.now - last).total_seconds()
    if elapsed < config.cooldown_seconds:
        remaining = int(config.cooldown_seconds - elapsed)
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_COOLDOWN_ACTIVE,
            reason=(
                f"action {proposal.action_type.value!r} is in cooldown; "
                f"{remaining}s remaining"
            ),
            stop_reason=StopReason.POLICY_STOP,
        )
    return None


__all__ = [
    "check_case_consistency",
    "check_duplicate_action",
    "check_amount_threshold",
    "check_low_confidence_high_value",
    "check_cooldown",
]
