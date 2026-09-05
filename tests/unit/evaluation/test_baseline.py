"""Baseline rule-only diagnoser: fixed, deterministic, workflow-permitted, no LLM."""

from __future__ import annotations

from decimal import Decimal

from revguard.domain import (
    ActionType,
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)
from revguard.evaluation.baseline import RuleOnlyDiagnoser
from revguard.policy.permissions import is_action_permitted


def _case(workflow: WorkflowType) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=workflow,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        amount_at_risk=Decimal("1000.00"),
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    return RecoveryCase(
        case_type=workflow,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=Decimal("1000.00"),
        currency=Currency.INR,
    )


def test_fixed_action_is_deterministic_and_case_scoped():
    diag = RuleOnlyDiagnoser()
    case = _case(WorkflowType.FAILED_SUBSCRIPTION)
    first = diag.diagnose(case)
    second = diag.diagnose(case)
    assert first.action_type == second.action_type == ActionType.RETRY_PAYMENT
    assert first.case_id == case.case_id


def test_baseline_action_is_permitted_for_every_workflow():
    diag = RuleOnlyDiagnoser()
    for workflow in WorkflowType:
        proposal = diag.diagnose(_case(workflow))
        # Every fixed baseline action must be on that workflow's permission whitelist,
        # so the baseline is never escalated merely for picking a disallowed action.
        assert is_action_permitted(workflow, proposal.action_type)


def test_baseline_ignores_attempt_history():
    diag = RuleOnlyDiagnoser()
    case = _case(WorkflowType.CHECKOUT_ABANDONMENT)
    a = diag.diagnose(case)
    case.attempt_count = 3  # a contextual strategy would adapt; the baseline does not
    b = diag.diagnose(case)
    assert a.action_type == b.action_type == ActionType.CREATE_PAYMENT_LINK
