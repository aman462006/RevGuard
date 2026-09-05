"""Terminal / stop / escalate conditions (POLICY_SPEC §2, §3).

These rules enforce the bounded, fail-closed nature of recovery: a case that is already
resolved, expired, out of attempts, or opted out must not spawn further actions, and the
AI's own recommend-stop / recommend-escalate signals are honoured as (non-binding) inputs
that the deterministic engine turns into STOP / ESCALATE.
"""

from __future__ import annotations

from revguard.domain import (
    ActionProposal,
    ActionType,
    CaseStatus,
    DecisionType,
    RecoveryCase,
    StopReason,
)
from revguard.policy.context import (
    PolicyConfig,
    PolicyContext,
    RuleId,
    RuleOutcome,
)
from revguard.policy.permissions import CONTACT_ACTIONS


def check_already_recovered(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """If the case is already recovered, no further recovery action is permitted."""
    if case.status is CaseStatus.RECOVERED or (
        case.amount_recovered >= case.amount_at_risk
    ):
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_ALREADY_RECOVERED,
            reason="case is already recovered; no further recovery action permitted",
            stop_reason=StopReason.ALREADY_RECOVERED,
        )
    return None


def check_terminal_case(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """A case in any terminal state accepts no further actions."""
    if case.status.is_terminal:
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_TERMINAL_CASE,
            reason=f"case is in terminal state {case.status.value!r}; no action permitted",
            stop_reason=StopReason.POLICY_STOP,
        )
    return None


def check_case_expired(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Stop cases past their expiry. Fail closed if expiry can't be evaluated."""
    if case.expires_at is None:
        return None
    if context.now is None:
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_MISSING_CONTEXT,
            reason="cannot evaluate case expiration without a current time; failing closed",
        )
    if context.now >= case.expires_at:
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_CASE_EXPIRED,
            reason=f"case expired at {case.expires_at.isoformat()}",
            stop_reason=StopReason.CASE_EXPIRED,
        )
    return None


def check_max_attempts(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Stop once the bounded attempt budget is exhausted."""
    if case.attempt_count >= config.max_attempts:
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_MAX_ATTEMPTS,
            reason=(
                f"attempt_count {case.attempt_count} has reached the maximum of "
                f"{config.max_attempts}"
            ),
            stop_reason=StopReason.MAX_ATTEMPTS_REACHED,
        )
    return None


def check_do_not_contact(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Block customer-contact actions when the customer has opted out."""
    if case.do_not_contact and proposal.action_type in CONTACT_ACTIONS:
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_DO_NOT_CONTACT,
            reason=(
                f"customer opted out (do_not_contact); contact action "
                f"{proposal.action_type.value!r} is blocked"
            ),
            stop_reason=StopReason.DO_NOT_CONTACT,
        )
    return None


def check_duplicate_promise(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Never record a second promise while one is still open — a duplicate promise must not be
    auto-approved. It is routed to a human (a renegotiation is a judgement call), deterministically.
    """
    if (
        proposal.action_type is ActionType.RECORD_PROMISE_TO_PAY
        and case.promise is not None
        and case.promise.status.is_open
    ):
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_DUPLICATE_PROMISE,
            reason=(
                f"an open promise-to-pay already exists (status "
                f"{case.promise.status.value!r}); not recording a duplicate"
            ),
        )
    return None


def check_ai_recommendation(
    proposal: ActionProposal,
    case: RecoveryCase,
    context: PolicyContext,
    config: PolicyConfig,
) -> RuleOutcome | None:
    """Turn the AI's non-binding recommend-stop / recommend-escalate / no-action signals
    into deterministic STOP / ESCALATE decisions (never an execution)."""
    action = proposal.action_type
    if action is ActionType.RECOMMEND_STOP:
        return RuleOutcome(
            decision=DecisionType.STOP,
            rule_id=RuleId.STOP_AI_RECOMMENDED_STOP,
            reason="AI recommended stopping; policy stops the case",
            stop_reason=StopReason.POLICY_STOP,
        )
    if action is ActionType.RECOMMEND_ESCALATION:
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_AI_RECOMMENDED_ESCALATION,
            reason="AI recommended escalation; routing to human queue",
        )
    if action is ActionType.NO_ACTION:
        return RuleOutcome(
            decision=DecisionType.ESCALATE,
            rule_id=RuleId.ESCALATE_NO_ACTION_REVIEW,
            reason=(
                "AI proposed no action on an unresolved case; escalating for human "
                "review (fail closed)"
            ),
        )
    return None


__all__ = [
    "check_already_recovered",
    "check_terminal_case",
    "check_case_expired",
    "check_max_attempts",
    "check_do_not_contact",
    "check_duplicate_promise",
    "check_ai_recommendation",
]
