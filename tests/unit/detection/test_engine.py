"""DetectionEngine — routing/grouping of a mixed event stream to the right detectors."""

from __future__ import annotations

from decimal import Decimal

from revguard.detection import DetectionEngine
from revguard.domain import EventType, WorkflowType


def test_routes_mixed_stream_to_correct_detectors(make_event):
    # One failing subscription + one abandoned checkout in the same stream.
    events = [
        make_event(
            EventType.SUBSCRIPTION_PAYMENT_FAILED, minute=0, amount=Decimal("1200.00"),
            subscription_id="sub_A", customer_id="cust_A",
            metadata={"failure_reason": "insufficient_funds"},
        ),
        make_event(
            EventType.SUBSCRIPTION_PAYMENT_FAILED, minute=60, amount=Decimal("1200.00"),
            subscription_id="sub_A", customer_id="cust_A",
            metadata={"failure_reason": "insufficient_funds"},
        ),
        make_event(
            EventType.CHECKOUT_ABANDONED, minute=0, amount=Decimal("15000.00"),
            order_id="order_B", customer_id="cust_B",
            metadata={"abandonment_stage": "payment_details"},
        ),
    ]
    signals = DetectionEngine().run(events)
    by_type = {s.signal_type for s in signals}
    assert by_type == {
        WorkflowType.FAILED_SUBSCRIPTION,
        WorkflowType.CHECKOUT_ABANDONMENT,
    }


def test_distinct_methods_are_grouped_separately(make_event):
    events = []
    for method in ("card", "upi"):
        for i in range(40):
            events.append(
                make_event(
                    EventType.PAYMENT_FAILED if i < 18 else EventType.PAYMENT_SUCCEEDED,
                    minute=i,
                    amount=Decimal("500.00"),
                    metadata={"method": method},
                )
            )
    signals = DetectionEngine().run(events)
    degradation = [
        s for s in signals if s.signal_type == WorkflowType.PAYMENT_DEGRADATION
    ]
    assert len(degradation) == 2
    assert {s.evidence["method"] for s in degradation} == {"card", "upi"}


def test_payment_success_without_method_does_not_trigger_degradation(make_event):
    # A subscription-clearing success must not be counted as a degradation payment.
    events = [
        make_event(
            EventType.PAYMENT_SUCCEEDED, minute=0, amount=Decimal("1200.00"),
            subscription_id="sub_A",
        )
    ]
    signals = DetectionEngine().run(events)
    assert signals == []


def test_empty_stream_yields_no_signals():
    assert DetectionEngine().run([]) == []
