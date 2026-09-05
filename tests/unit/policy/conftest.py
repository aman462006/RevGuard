"""Factories for policy-engine unit tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from revguard.domain import (
    ActionProposal,
    ActionType,
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


@pytest.fixture
def make_case() -> Callable[..., RecoveryCase]:
    def _make(**overrides) -> RecoveryCase:
        workflow = overrides.pop("case_type", WorkflowType.FAILED_SUBSCRIPTION)
        amount = overrides.pop("amount_at_risk", Decimal("1500.00"))
        signal = RevenueRiskSignal(
            signal_type=workflow,
            risk_level=RiskLevel.HIGH,
            customer_id="cust_1",
            amount_at_risk=amount,
            currency=Currency.INR,
            source_event_ids=["evt_1"],
        )
        defaults = dict(
            case_type=workflow,
            customer_id="cust_1",
            signal=signal,
            amount_at_risk=amount,
            currency=Currency.INR,
        )
        defaults.update(overrides)
        return RecoveryCase(**defaults)

    return _make


@pytest.fixture
def make_proposal() -> Callable[..., ActionProposal]:
    def _make(
        case: RecoveryCase,
        action: ActionType = ActionType.RETRY_PAYMENT,
        confidence: float = 0.9,
        **overrides,
    ) -> ActionProposal:
        return ActionProposal(
            case_id=case.case_id,
            action_type=action,
            rationale="test rationale",
            confidence=confidence,
            **overrides,
        )

    return _make
