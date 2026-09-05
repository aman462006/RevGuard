"""Detector B — failed subscription / mandate: positive, negative, boundary cases."""

from __future__ import annotations

from decimal import Decimal

from revguard.detection import FailedSubscriptionDetector
from revguard.domain import Currency, EventType, RiskLevel, WorkflowType


def _failures(make_event, count, amount=Decimal("1200.00"), sub="sub_1", cust="cust_1"):
    return [
        make_event(
            EventType.SUBSCRIPTION_PAYMENT_FAILED,
            minute=i * 60,
            amount=amount,
            subscription_id=sub,
            customer_id=cust,
            metadata={"failure_reason": "insufficient_funds", "attempt": i + 1},
        )
        for i in range(count)
    ]


def test_single_failure_low(make_event):
    signal = FailedSubscriptionDetector().detect(_failures(make_event, 1))
    assert signal is not None
    assert signal.signal_type == WorkflowType.FAILED_SUBSCRIPTION
    assert signal.risk_level == RiskLevel.LOW
    assert signal.subscription_id == "sub_1"
    assert signal.customer_id == "cust_1"
    assert signal.amount_at_risk == Decimal("1200.00")
    assert signal.evidence["failure_count"] == 1
    assert signal.evidence["last_failure_reason"] == "insufficient_funds"


def test_two_failures_medium(make_event):
    signal = FailedSubscriptionDetector().detect(_failures(make_event, 2))
    assert signal.risk_level == RiskLevel.MEDIUM


def test_three_failures_high(make_event):
    signal = FailedSubscriptionDetector().detect(_failures(make_event, 3))
    assert signal.risk_level == RiskLevel.HIGH


def test_four_failures_critical(make_event):
    signal = FailedSubscriptionDetector().detect(_failures(make_event, 4))
    assert signal.risk_level == RiskLevel.CRITICAL


def test_high_value_bumps_severity(make_event):
    # 2 failures would be MEDIUM, but a >= 5000 INR mandate bumps to HIGH.
    signal = FailedSubscriptionDetector().detect(
        _failures(make_event, 2, amount=Decimal("6000.00"))
    )
    assert signal.risk_level == RiskLevel.HIGH


def test_high_value_bump_only_for_inr(make_event):
    events = [
        make_event(
            EventType.SUBSCRIPTION_PAYMENT_FAILED,
            minute=i * 60,
            amount=Decimal("6000.00"),
            currency=Currency.USD,
            subscription_id="sub_x",
            customer_id="cust_x",
            metadata={"failure_reason": "card_expired"},
        )
        for i in range(2)
    ]
    signal = FailedSubscriptionDetector().detect(events)
    assert signal.currency == Currency.USD
    assert signal.risk_level == RiskLevel.MEDIUM  # no INR bump applied


def test_recovered_after_failure_no_signal(make_event):
    events = _failures(make_event, 2)
    events.append(
        make_event(
            EventType.PAYMENT_SUCCEEDED,
            minute=300,
            amount=Decimal("1200.00"),
            subscription_id="sub_1",
            customer_id="cust_1",
        )
    )
    assert FailedSubscriptionDetector().detect(events) is None


def test_no_failures_no_signal(make_event):
    events = [
        make_event(
            EventType.PAYMENT_SUCCEEDED,
            minute=0,
            amount=Decimal("1200.00"),
            subscription_id="sub_1",
        )
    ]
    assert FailedSubscriptionDetector().detect(events) is None
