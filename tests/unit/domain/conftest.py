"""Shared factories for domain unit tests.

Small helpers that build valid instances so each test can mutate one field to prove a
specific rule, keeping tests focused on behavior rather than construction boilerplate.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from revguard.domain import (
    ActionType,
    Currency,
    Event,
    EventSource,
    EventType,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


@pytest.fixture
def valid_event() -> Event:
    return Event(
        event_type=EventType.SUBSCRIPTION_PAYMENT_FAILED,
        source=EventSource.SYNTHETIC,
        customer_id="cust_1",
        subscription_id="sub_1",
        amount=Decimal("1500.00"),
        currency=Currency.INR,
    )


@pytest.fixture
def valid_signal(valid_event: Event) -> RevenueRiskSignal:
    return RevenueRiskSignal(
        signal_type=WorkflowType.FAILED_SUBSCRIPTION,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        subscription_id="sub_1",
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        source_event_ids=[valid_event.event_id],
        confidence=0.9,
    )


@pytest.fixture
def valid_case(valid_signal: RevenueRiskSignal) -> RecoveryCase:
    return RecoveryCase(
        case_type=WorkflowType.FAILED_SUBSCRIPTION,
        customer_id="cust_1",
        signal=valid_signal,
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        next_action=ActionType.RETRY_PAYMENT,
    )
