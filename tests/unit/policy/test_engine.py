"""PolicyEngine decision behaviour (POLICY_SPEC §1–§5).

The engine is the sole approval gate: fail-closed, deterministic, side-effect free, and an
AI proposal never bypasses a policy rule.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from revguard.domain import (
    ActionType,
    DecisionType,
    PolicyDecision,
    StopReason,
    WorkflowType,
    utcnow,
)
from revguard.policy import (
    ActionRecord,
    PolicyConfig,
    PolicyContext,
    PolicyEngine,
)
from revguard.policy.context import RuleId

# ---------------------------------------------------------------------------
# Approve path — one permitted action per workflow
# ---------------------------------------------------------------------------

_PERMITTED_BY_WORKFLOW = {
    WorkflowType.PAYMENT_DEGRADATION: ActionType.WAIT,
    WorkflowType.FAILED_SUBSCRIPTION: ActionType.RETRY_PAYMENT,
    WorkflowType.CHECKOUT_ABANDONMENT: ActionType.CREATE_PAYMENT_LINK,
    WorkflowType.OVERDUE_RECEIVABLE: ActionType.SEND_REMINDER,
}

_DISALLOWED_BY_WORKFLOW = {
    WorkflowType.PAYMENT_DEGRADATION: ActionType.RETRY_PAYMENT,
    WorkflowType.FAILED_SUBSCRIPTION: ActionType.RECORD_PROMISE_TO_PAY,
    WorkflowType.CHECKOUT_ABANDONMENT: ActionType.RETRY_PAYMENT,
    WorkflowType.OVERDUE_RECEIVABLE: ActionType.CREATE_PAYMENT_LINK,
}


@pytest.mark.parametrize("workflow, action", list(_PERMITTED_BY_WORKFLOW.items()))
def test_permitted_action_is_approved(make_case, make_proposal, workflow, action):
    case = make_case(case_type=workflow)
    decision = PolicyEngine().evaluate(make_proposal(case, action=action), case)
    assert decision.decision is DecisionType.APPROVE
    assert decision.proposed_action is action
    assert decision.matched_rules == [RuleId.APPROVE_PERMITTED_ACTION.value]


@pytest.mark.parametrize("workflow, action", list(_DISALLOWED_BY_WORKFLOW.items()))
def test_disallowed_action_escalates(make_case, make_proposal, workflow, action):
    case = make_case(case_type=workflow)
    decision = PolicyEngine().evaluate(make_proposal(case, action=action), case)
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_PERMISSION_DENIED.value]


# ---------------------------------------------------------------------------
# Stopping rules
# ---------------------------------------------------------------------------


def test_max_attempts_reached_stops(make_case, make_proposal):
    case = make_case(attempt_count=4)
    decision = PolicyEngine(PolicyConfig(max_attempts=4)).evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT), case
    )
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_MAX_ATTEMPTS.value]


def test_already_recovered_stops(make_case, make_proposal):
    case = make_case(amount_recovered=Decimal("1500.00"))  # == amount_at_risk
    decision = PolicyEngine().evaluate(make_proposal(case), case)
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_ALREADY_RECOVERED.value]


def test_terminal_case_stops(make_case, make_proposal):
    case = make_case(
        status="stopped", stopped_at=utcnow(), stop_reason=StopReason.MANUAL_STOP
    )
    decision = PolicyEngine().evaluate(make_proposal(case), case)
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_TERMINAL_CASE.value]


def test_expired_case_stops(make_case, make_proposal):
    now = utcnow()
    case = make_case(expires_at=now - timedelta(hours=1))
    decision = PolicyEngine().evaluate(
        make_proposal(case), case, PolicyContext(now=now)
    )
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_CASE_EXPIRED.value]


def test_do_not_contact_blocks_contact_action(make_case, make_proposal):
    case = make_case(case_type=WorkflowType.OVERDUE_RECEIVABLE, do_not_contact=True)
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.SEND_REMINDER), case
    )
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_DO_NOT_CONTACT.value]


def test_do_not_contact_does_not_block_non_contact_action(make_case, make_proposal):
    # A retry is not a customer-contact action, so opt-out does not stop it.
    case = make_case(case_type=WorkflowType.FAILED_SUBSCRIPTION, do_not_contact=True)
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT), case
    )
    assert decision.decision is DecisionType.APPROVE


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


def test_amount_over_threshold_escalates(make_case, make_proposal):
    case = make_case(amount_at_risk=Decimal("60000.00"))
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT), case
    )
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_AMOUNT_THRESHOLD.value]
    assert decision.threshold_amount == Decimal("50000")


def test_low_confidence_high_value_escalates(make_case, make_proposal):
    case = make_case(amount_at_risk=Decimal("30000.00"))
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT, confidence=0.2), case
    )
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_LOW_CONFIDENCE_HIGH_VALUE.value]


def test_cooldown_active_stops(make_case, make_proposal):
    now = utcnow()
    case = make_case()
    ctx = PolicyContext(
        now=now,
        action_history=(
            ActionRecord(ActionType.RETRY_PAYMENT, now - timedelta(minutes=10)),
        ),
    )
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT), case, ctx
    )
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_COOLDOWN_ACTIVE.value]


def test_cooldown_elapsed_allows_approval(make_case, make_proposal):
    now = utcnow()
    case = make_case()
    ctx = PolicyContext(
        now=now,
        action_history=(
            ActionRecord(ActionType.RETRY_PAYMENT, now - timedelta(hours=2)),
        ),
    )
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT), case, ctx
    )
    assert decision.decision is DecisionType.APPROVE


def test_duplicate_action_is_not_approved(make_case, make_proposal):
    case = make_case()
    proposal = make_proposal(
        case, action=ActionType.RETRY_PAYMENT, parameters={"idempotency_key": "k1"}
    )
    ctx = PolicyContext(executed_idempotency_keys=frozenset({"k1"}))
    decision = PolicyEngine().evaluate(proposal, case, ctx)
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_DUPLICATE_ACTION.value]


# ---------------------------------------------------------------------------
# AI recommendation intents
# ---------------------------------------------------------------------------


def test_recommend_escalation_escalates(make_case, make_proposal):
    case = make_case()
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RECOMMEND_ESCALATION), case
    )
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_AI_RECOMMENDED_ESCALATION.value]


def test_recommend_stop_stops(make_case, make_proposal):
    case = make_case()
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RECOMMEND_STOP), case
    )
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_AI_RECOMMENDED_STOP.value]


def test_no_action_escalates_for_review(make_case, make_proposal):
    case = make_case()
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.NO_ACTION), case
    )
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_NO_ACTION_REVIEW.value]


# ---------------------------------------------------------------------------
# Fail-closed / missing context
# ---------------------------------------------------------------------------


def test_case_id_mismatch_fails_closed(make_case, make_proposal):
    case = make_case()
    other = make_case()
    proposal = make_proposal(other, action=ActionType.RETRY_PAYMENT)  # different case_id
    decision = PolicyEngine().evaluate(proposal, case)
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_CONTEXT_MISMATCH.value]


def test_missing_now_with_expiry_fails_closed(make_case, make_proposal):
    case = make_case(expires_at=utcnow() + timedelta(hours=1))
    decision = PolicyEngine().evaluate(
        make_proposal(case), case, PolicyContext(now=None)
    )
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_MISSING_CONTEXT.value]


def test_missing_now_with_action_history_fails_closed(make_case, make_proposal):
    case = make_case()
    ctx = PolicyContext(
        now=None,
        action_history=(ActionRecord(ActionType.RETRY_PAYMENT, utcnow()),),
    )
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT), case, ctx
    )
    assert decision.decision is DecisionType.ESCALATE
    assert decision.matched_rules == [RuleId.ESCALATE_MISSING_CONTEXT.value]


# ---------------------------------------------------------------------------
# AI confidence never overrides policy
# ---------------------------------------------------------------------------


def test_confidence_does_not_bypass_permission(make_case, make_proposal):
    case = make_case(case_type=WorkflowType.PAYMENT_DEGRADATION)
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT, confidence=1.0), case
    )
    assert decision.decision is DecisionType.ESCALATE


def test_confidence_does_not_bypass_max_attempts(make_case, make_proposal):
    case = make_case(attempt_count=10)
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT, confidence=1.0), case
    )
    assert decision.decision is DecisionType.STOP


def test_confidence_does_not_bypass_already_recovered(make_case, make_proposal):
    case = make_case(amount_recovered=Decimal("1500.00"))
    decision = PolicyEngine().evaluate(
        make_proposal(case, action=ActionType.RETRY_PAYMENT, confidence=1.0), case
    )
    assert decision.decision is DecisionType.STOP


# ---------------------------------------------------------------------------
# Coverage: every ActionType has an explicit outcome; determinism; provenance
# ---------------------------------------------------------------------------

_EXPECTED_OUTCOME = {
    ActionType.RETRY_PAYMENT: (WorkflowType.FAILED_SUBSCRIPTION, DecisionType.APPROVE),
    ActionType.CREATE_PAYMENT_LINK: (
        WorkflowType.FAILED_SUBSCRIPTION,
        DecisionType.APPROVE,
    ),
    ActionType.SEND_REMINDER: (WorkflowType.FAILED_SUBSCRIPTION, DecisionType.APPROVE),
    ActionType.RECORD_PROMISE_TO_PAY: (
        WorkflowType.OVERDUE_RECEIVABLE,
        DecisionType.APPROVE,
    ),
    ActionType.WAIT: (WorkflowType.PAYMENT_DEGRADATION, DecisionType.APPROVE),
    ActionType.NO_ACTION: (WorkflowType.FAILED_SUBSCRIPTION, DecisionType.ESCALATE),
    ActionType.RECOMMEND_ESCALATION: (
        WorkflowType.FAILED_SUBSCRIPTION,
        DecisionType.ESCALATE,
    ),
    ActionType.RECOMMEND_STOP: (WorkflowType.FAILED_SUBSCRIPTION, DecisionType.STOP),
}


def test_every_action_type_has_explicit_outcome(make_case, make_proposal):
    # The map must cover the whole enum...
    assert set(_EXPECTED_OUTCOME) == set(ActionType)
    engine = PolicyEngine()
    for action, (workflow, expected) in _EXPECTED_OUTCOME.items():
        case = make_case(case_type=workflow)
        decision = engine.evaluate(make_proposal(case, action=action), case)
        assert isinstance(decision, PolicyDecision)
        assert decision.decision is expected, action


def test_decision_always_carries_rule_and_reason(make_case, make_proposal):
    case = make_case()
    decision = PolicyEngine().evaluate(make_proposal(case), case)
    assert decision.matched_rules and all(decision.matched_rules)
    assert decision.reason


def test_engine_is_deterministic_and_pure(make_case, make_proposal):
    case = make_case()
    proposal = make_proposal(case, action=ActionType.RETRY_PAYMENT)
    ctx = PolicyContext(now=utcnow())
    engine = PolicyEngine()

    d1 = engine.evaluate(proposal, case, ctx)
    d2 = engine.evaluate(proposal, case, ctx)

    # Same inputs -> same decision content (ids/timestamps aside).
    assert d1.decision == d2.decision
    assert d1.matched_rules == d2.matched_rules
    assert d1.reason == d2.reason
    # The proposal and case are not mutated by evaluation.
    assert case.status.value == "detected"
    assert proposal.action_type is ActionType.RETRY_PAYMENT
