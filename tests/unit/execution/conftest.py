"""Factories and fakes for execution-layer tests."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from revguard.domain import (
    ActionProposal,
    ActionType,
    Currency,
    DecisionType,
    PolicyDecision,
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
            evidence={"failure_count": 2},
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
        **overrides,
    ) -> ActionProposal:
        defaults = dict(
            case_id=case.case_id,
            action_type=action,
            rationale=f"test proposal for {action.value}",
            confidence=0.8,
        )
        defaults.update(overrides)
        return ActionProposal(**defaults)

    return _make


@pytest.fixture
def make_decision() -> Callable[..., PolicyDecision]:
    """Build a PolicyDecision directly (bypassing the engine) for controlled executor tests."""

    def _make(
        case: RecoveryCase,
        proposal: ActionProposal,
        decision: DecisionType = DecisionType.APPROVE,
        **overrides,
    ) -> PolicyDecision:
        defaults = dict(
            case_id=case.case_id,
            decision=decision,
            proposed_action=proposal.action_type,
            reason=f"test decision {decision.value}",
            matched_rules=["test.rule"],
        )
        defaults.update(overrides)
        return PolicyDecision(**defaults)

    return _make


class RecordingAuditSink:
    """Captures audit events in memory (satisfies the AuditSink protocol)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def record_event(self, **kwargs) -> None:  # noqa: ANN003
        self.events.append(kwargs)


@pytest.fixture
def audit_sink() -> RecordingAuditSink:
    return RecordingAuditSink()
