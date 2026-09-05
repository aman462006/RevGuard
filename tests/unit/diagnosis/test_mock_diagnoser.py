"""MockDiagnoser: deterministic, valid proposals across all four workflows."""

from __future__ import annotations

import pytest

from revguard.diagnosis import MockDiagnoser
from revguard.domain import ActionProposal, ActionType, RiskLevel, WorkflowType


@pytest.mark.parametrize("workflow", list(WorkflowType))
def test_produces_valid_proposal_for_every_workflow(make_case, workflow):
    case = make_case(workflow)
    proposal = MockDiagnoser().diagnose(case)

    assert isinstance(proposal, ActionProposal)
    assert isinstance(proposal.action_type, ActionType)  # always an allowed enum member
    assert proposal.case_id == case.case_id
    assert proposal.rationale
    assert 0.0 <= proposal.confidence <= 1.0


def test_is_deterministic(make_case):
    case = make_case(WorkflowType.FAILED_SUBSCRIPTION)
    a = MockDiagnoser().diagnose(case)
    b = MockDiagnoser().diagnose(case)
    assert a.action_type == b.action_type
    assert a.rationale == b.rationale
    assert a.confidence == b.confidence


def test_failed_subscription_follows_bounded_sequence(make_case):
    diag = MockDiagnoser()
    assert diag.diagnose(
        make_case(WorkflowType.FAILED_SUBSCRIPTION, attempt_count=0)
    ).action_type is ActionType.RETRY_PAYMENT
    assert diag.diagnose(
        make_case(WorkflowType.FAILED_SUBSCRIPTION, attempt_count=1)
    ).action_type is ActionType.CREATE_PAYMENT_LINK
    assert diag.diagnose(
        make_case(WorkflowType.FAILED_SUBSCRIPTION, attempt_count=2)
    ).action_type is ActionType.SEND_REMINDER
    assert diag.diagnose(
        make_case(WorkflowType.FAILED_SUBSCRIPTION, attempt_count=5)
    ).action_type is ActionType.RECOMMEND_ESCALATION


def test_overdue_receivable_actions(make_case):
    diag = MockDiagnoser()
    assert diag.diagnose(
        make_case(WorkflowType.OVERDUE_RECEIVABLE, attempt_count=0)
    ).action_type is ActionType.SEND_REMINDER
    assert diag.diagnose(
        make_case(WorkflowType.OVERDUE_RECEIVABLE, attempt_count=1)
    ).action_type is ActionType.RECORD_PROMISE_TO_PAY


def test_checkout_abandonment_actions(make_case):
    diag = MockDiagnoser()
    assert diag.diagnose(
        make_case(WorkflowType.CHECKOUT_ABANDONMENT, attempt_count=0)
    ).action_type is ActionType.CREATE_PAYMENT_LINK
    assert diag.diagnose(
        make_case(WorkflowType.CHECKOUT_ABANDONMENT, attempt_count=9)
    ).action_type is ActionType.RECOMMEND_STOP


def test_payment_degradation_escalates_high_risk(make_case):
    diag = MockDiagnoser()
    assert diag.diagnose(
        make_case(WorkflowType.PAYMENT_DEGRADATION, risk=RiskLevel.CRITICAL)
    ).action_type is ActionType.RECOMMEND_ESCALATION
    assert diag.diagnose(
        make_case(WorkflowType.PAYMENT_DEGRADATION, risk=RiskLevel.LOW)
    ).action_type is ActionType.WAIT


def test_respects_do_not_contact(make_case):
    # Attempt 0 for B would be RETRY (not contact) — force a contact action via attempt 2.
    case = make_case(WorkflowType.OVERDUE_RECEIVABLE, attempt_count=0, do_not_contact=True)
    proposal = MockDiagnoser().diagnose(case)
    # SEND_REMINDER is a contact action -> escalates instead.
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION


def test_recovery_actions_carry_expected_amount(make_case):
    proposal = MockDiagnoser().diagnose(
        make_case(WorkflowType.FAILED_SUBSCRIPTION, attempt_count=0)
    )
    assert proposal.action_type is ActionType.RETRY_PAYMENT
    assert proposal.expected_recovery_amount == make_case(
        WorkflowType.FAILED_SUBSCRIPTION
    ).amount_at_risk
    assert proposal.expected_recovery_currency is not None
