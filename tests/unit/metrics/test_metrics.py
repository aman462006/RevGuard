"""Batch metrics: exact aggregation, no double counting, and side-by-side comparison."""

from __future__ import annotations

from decimal import Decimal

from revguard.domain import (
    CaseStatus,
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    StopReason,
    WorkflowType,
    utcnow,
)
from revguard.metrics import compare, compute_metrics


def _case(
    *,
    workflow: WorkflowType = WorkflowType.FAILED_SUBSCRIPTION,
    amount: Decimal = Decimal("1000.00"),
    recovered: Decimal = Decimal("0"),
    status: CaseStatus = CaseStatus.ACTION_PENDING,
) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=workflow,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        amount_at_risk=amount,
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    extra = {}
    if status is CaseStatus.STOPPED:
        extra = dict(stopped_at=utcnow(), stop_reason=StopReason.MAX_ATTEMPTS_REACHED)
    elif status is CaseStatus.ESCALATED:
        extra = dict(escalated_at=utcnow(), escalation_reason="human review")
    return RecoveryCase(
        case_type=workflow,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=amount,
        currency=Currency.INR,
        amount_recovered=recovered,
        status=status,
        **extra,
    )


def test_empty_batch_is_all_zero():
    m = compute_metrics("baseline", [])
    assert m.total_cases == 0
    assert m.revenue_at_risk == Decimal("0")
    assert m.recovered_amount == Decimal("0")
    assert m.unresolved_amount == Decimal("0")
    assert m.recovery_rate == Decimal("0.0000")  # no ZeroDivisionError
    assert m.recovered == m.escalated == m.stopped == m.failed == 0
    assert m.unresolved_cases == 0
    assert m.by_workflow == {}


def test_recovered_amount_rate_and_counts():
    cases = [
        _case(amount=Decimal("1000.00"), recovered=Decimal("1000.00"),
              status=CaseStatus.RECOVERED),
        _case(amount=Decimal("2000.00"), status=CaseStatus.STOPPED),
        _case(amount=Decimal("1000.00"), status=CaseStatus.ESCALATED),
    ]
    m = compute_metrics("revguard", cases)
    assert m.total_cases == 3
    assert m.revenue_at_risk == Decimal("4000.00")
    assert m.recovered_amount == Decimal("1000.00")
    assert m.unresolved_amount == Decimal("3000.00")
    assert m.recovery_rate == Decimal("0.2500")
    assert (m.recovered, m.stopped, m.escalated) == (1, 1, 1)
    assert m.unresolved_cases == 2  # everything not RECOVERED


def test_partial_recovery_is_not_double_counted():
    # A verified partial recovery contributes exactly its recovered amount, once.
    cases = [
        _case(amount=Decimal("1000.00"), recovered=Decimal("400.00"),
              status=CaseStatus.RECOVERED),
        _case(amount=Decimal("500.00"), recovered=Decimal("500.00"),
              status=CaseStatus.RECOVERED),
    ]
    m = compute_metrics("revguard", cases)
    assert m.recovered_amount == Decimal("900.00")  # 400 + 500, not 1500
    assert m.revenue_at_risk == Decimal("1500.00")
    assert m.unresolved_amount == Decimal("600.00")
    assert m.recovered == 2


def test_open_case_is_unresolved_and_counted_open():
    cases = [_case(amount=Decimal("1000.00"), status=CaseStatus.WAITING)]
    m = compute_metrics("revguard", cases)
    assert m.open_cases == 1
    assert m.recovered == 0
    assert m.unresolved_cases == 1
    assert m.recovery_rate == Decimal("0.0000")
    assert m.unresolved_amount == Decimal("1000.00")


def test_workflow_breakdown_is_per_workflow():
    cases = [
        _case(workflow=WorkflowType.FAILED_SUBSCRIPTION, amount=Decimal("1000.00"),
              recovered=Decimal("1000.00"), status=CaseStatus.RECOVERED),
        _case(workflow=WorkflowType.CHECKOUT_ABANDONMENT, amount=Decimal("2000.00"),
              status=CaseStatus.STOPPED),
    ]
    m = compute_metrics("revguard", cases)
    assert set(m.by_workflow) == {
        WorkflowType.FAILED_SUBSCRIPTION,
        WorkflowType.CHECKOUT_ABANDONMENT,
    }
    sub = m.by_workflow[WorkflowType.FAILED_SUBSCRIPTION]
    assert sub.recovered_amount == Decimal("1000.00")
    assert sub.recovery_rate == Decimal("1.0000")
    checkout = m.by_workflow[WorkflowType.CHECKOUT_ABANDONMENT]
    assert checkout.recovered_amount == Decimal("0")
    assert checkout.recovery_rate == Decimal("0.0000")
    assert checkout.stopped == 1


def test_compare_computes_signed_deltas():
    baseline = compute_metrics(
        "baseline",
        [_case(amount=Decimal("1000.00"), status=CaseStatus.STOPPED)],
    )
    revguard = compute_metrics(
        "revguard",
        [_case(amount=Decimal("1000.00"), recovered=Decimal("1000.00"),
               status=CaseStatus.RECOVERED)],
    )
    c = compare(baseline, revguard)
    assert c.delta.revenue_recovered_delta == Decimal("1000.00")
    assert c.delta.recovered_cases_delta == 1
    assert c.delta.stopped_delta == -1
    assert c.delta.recovery_rate_delta == Decimal("1.0000")
    assert c.baseline is baseline and c.revguard is revguard
