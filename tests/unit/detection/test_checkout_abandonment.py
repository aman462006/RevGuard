"""Detector C — checkout abandonment: positive, negative, boundary cases."""

from __future__ import annotations

from decimal import Decimal

from revguard.detection import CheckoutAbandonmentDetector
from revguard.domain import EventType, RiskLevel, WorkflowType


def _abandon(make_event, amount, count=1, order="order_1", cust="cust_1", stage="cart"):
    return [
        make_event(
            EventType.CHECKOUT_ABANDONED,
            minute=i * 30,
            amount=Decimal(amount),
            order_id=order,
            customer_id=cust,
            metadata={"abandonment_stage": stage, "attempt": i + 1},
        )
        for i in range(count)
    ]


def test_small_cart_low(make_event):
    signal = CheckoutAbandonmentDetector().detect(_abandon(make_event, "500.00"))
    assert signal is not None
    assert signal.signal_type == WorkflowType.CHECKOUT_ABANDONMENT
    assert signal.risk_level == RiskLevel.LOW
    assert signal.order_id == "order_1"
    assert signal.amount_at_risk == Decimal("500.00")
    assert signal.evidence["abandon_count"] == 1


def test_medium_cart_medium(make_event):
    signal = CheckoutAbandonmentDetector().detect(_abandon(make_event, "3000.00"))
    assert signal.risk_level == RiskLevel.MEDIUM


def test_large_cart_high(make_event):
    signal = CheckoutAbandonmentDetector().detect(_abandon(make_event, "15000.00"))
    assert signal.risk_level == RiskLevel.HIGH


def test_boundary_medium_threshold(make_event):
    # Exactly at the medium threshold (2000) -> MEDIUM.
    signal = CheckoutAbandonmentDetector().detect(_abandon(make_event, "2000.00"))
    assert signal.risk_level == RiskLevel.MEDIUM


def test_repeat_abandonment_bumps(make_event):
    signal = CheckoutAbandonmentDetector().detect(
        _abandon(make_event, "3000.00", count=3)
    )
    assert signal.evidence["abandon_count"] == 3
    assert signal.risk_level == RiskLevel.HIGH  # MEDIUM bumped by repeat abandonment


def test_below_floor_no_signal(make_event):
    assert CheckoutAbandonmentDetector().detect(_abandon(make_event, "50.00")) is None


def test_completed_after_abandonment_no_signal(make_event):
    events = _abandon(make_event, "3000.00")
    events.append(
        make_event(
            EventType.PAYMENT_SUCCEEDED,
            minute=60,
            amount=Decimal("3000.00"),
            order_id="order_1",
            customer_id="cust_1",
        )
    )
    assert CheckoutAbandonmentDetector().detect(events) is None


def test_no_abandonment_no_signal(make_event):
    events = [
        make_event(
            EventType.PAYMENT_SUCCEEDED, minute=0, amount=Decimal("3000.00"),
            order_id="order_1",
        )
    ]
    assert CheckoutAbandonmentDetector().detect(events) is None
