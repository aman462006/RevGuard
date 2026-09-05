"""Detector A — payment degradation: positive, negative, and boundary cases."""

from __future__ import annotations

from decimal import Decimal

from revguard.detection import PaymentDegradationDetector
from revguard.domain import Currency, EventType, RiskLevel, WorkflowType


def _window(make_event, method, total, failures, amount=Decimal("500.00")):
    events = []
    for i in range(total):
        is_fail = i < failures
        events.append(
            make_event(
                EventType.PAYMENT_FAILED if is_fail else EventType.PAYMENT_SUCCEEDED,
                minute=i,
                amount=amount,
                metadata={"method": method},
            )
        )
    return events


def test_high_degradation_emits_signal(make_event):
    events = _window(make_event, "card", total=40, failures=18)
    signal = PaymentDegradationDetector().detect(events)

    assert signal is not None
    assert signal.signal_type == WorkflowType.PAYMENT_DEGRADATION
    assert signal.risk_level == RiskLevel.HIGH
    assert signal.currency == Currency.INR
    # amount at risk = sum of the 18 failed payments.
    assert signal.amount_at_risk == Decimal("500.00") * 18
    assert signal.evidence["method"] == "card"
    assert signal.evidence["sample_size"] == 40
    assert signal.evidence["failure_count"] == 18
    assert len(signal.source_event_ids) == 40


def test_critical_degradation(make_event):
    events = _window(make_event, "upi", total=40, failures=24)
    signal = PaymentDegradationDetector().detect(events)
    assert signal is not None
    assert signal.risk_level == RiskLevel.CRITICAL


def test_boundary_exact_threshold_emits_medium(make_event):
    # 10/40 = 0.25 observed - 0.10 baseline = 0.15 == degradation_delta -> emits MEDIUM.
    events = _window(make_event, "card", total=40, failures=10)
    signal = PaymentDegradationDetector().detect(events)
    assert signal is not None
    assert signal.risk_level == RiskLevel.MEDIUM


def test_normal_failure_rate_no_signal(make_event):
    events = _window(make_event, "card", total=30, failures=3)  # 10% == baseline
    assert PaymentDegradationDetector().detect(events) is None


def test_insufficient_sample_no_signal(make_event):
    # High failure rate but below the minimum sample size -> not enough evidence.
    events = _window(make_event, "card", total=15, failures=10)
    assert PaymentDegradationDetector().detect(events) is None


def test_just_below_threshold_no_signal(make_event):
    # 9/40 = 0.225 -> delta 0.125 < 0.15 -> no signal (boundary, negative side).
    events = _window(make_event, "card", total=40, failures=9)
    assert PaymentDegradationDetector().detect(events) is None


def test_mixed_currency_window_no_signal(make_event):
    events = _window(make_event, "card", total=40, failures=18)
    # Corrupt one failed event's currency so the group is ambiguous.
    events[0] = make_event(
        EventType.PAYMENT_FAILED, minute=0, amount=Decimal("500.00"),
        currency=Currency.USD, metadata={"method": "card"},
    )
    assert PaymentDegradationDetector().detect(events) is None
