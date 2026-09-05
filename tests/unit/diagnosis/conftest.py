"""Factories and fakes for diagnosis-layer tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from revguard.domain import (
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


@pytest.fixture
def make_case() -> Callable[..., RecoveryCase]:
    def _make(
        workflow: WorkflowType = WorkflowType.FAILED_SUBSCRIPTION,
        *,
        amount: Decimal = Decimal("1500.00"),
        risk: RiskLevel = RiskLevel.HIGH,
        **overrides,
    ) -> RecoveryCase:
        signal = RevenueRiskSignal(
            signal_type=workflow,
            risk_level=risk,
            customer_id="cust_1",
            amount_at_risk=amount,
            currency=Currency.INR,
            evidence={"failure_count": 2, "days_overdue": 40},
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


class FakeContentBlock:
    """Mimics an Anthropic tool_use content block (object-shaped)."""

    def __init__(self, *, block_type: str, name: str | None, input: dict | None) -> None:
        self.type = block_type
        self.name = name
        self.input = input


class FakeMessage:
    def __init__(self, content: list) -> None:
        self.content = content


class FakeAnthropicClient:
    """Stands in for anthropic.Anthropic — returns a canned response or raises."""

    def __init__(self, *, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.calls: list[dict] = []
        self.messages = self  # so client.messages.create(...) works

    def create(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


@pytest.fixture
def tool_use_response() -> Callable[[dict, str], FakeMessage]:
    from revguard.diagnosis.prompts import SUBMIT_TOOL_NAME

    def _make(tool_input: dict, name: str = SUBMIT_TOOL_NAME) -> FakeMessage:
        return FakeMessage(
            [FakeContentBlock(block_type="tool_use", name=name, input=tool_input)]
        )

    return _make
