"""Fixtures for persistence/audit integration tests.

Provides a temporary **file-backed** SQLite database (so a fresh session can re-open it)
and small factories that build valid domain objects.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from revguard.domain import (
    ActionType,
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)
from revguard.persistence import Database


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'revguard_test.db'}"


@pytest.fixture
def database(db_url: str) -> Database:
    db = Database(db_url)
    db.create_all()
    try:
        yield db
    finally:
        db.dispose()


@pytest.fixture
def make_signal() -> Callable[..., RevenueRiskSignal]:
    def _make(**overrides) -> RevenueRiskSignal:
        defaults = dict(
            signal_type=WorkflowType.FAILED_SUBSCRIPTION,
            risk_level=RiskLevel.HIGH,
            customer_id="cust_1",
            subscription_id="sub_1",
            amount_at_risk=Decimal("1500.00"),
            currency=Currency.INR,
            evidence={"failure_count": 2},
            source_event_ids=["evt_1", "evt_2"],
            confidence=0.9,
        )
        defaults.update(overrides)
        return RevenueRiskSignal(**defaults)

    return _make


@pytest.fixture
def make_case(make_signal) -> Callable[..., RecoveryCase]:
    def _make(**overrides) -> RecoveryCase:
        defaults = dict(
            case_type=WorkflowType.FAILED_SUBSCRIPTION,
            customer_id="cust_1",
            signal=make_signal(),
            amount_at_risk=Decimal("1500.00"),
            currency=Currency.INR,
            next_action=ActionType.RETRY_PAYMENT,
            metadata={"origin": "synthetic"},
        )
        defaults.update(overrides)
        return RecoveryCase(**defaults)

    return _make
