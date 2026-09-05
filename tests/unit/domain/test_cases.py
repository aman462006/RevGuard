"""RecoveryCase + CaseStatus: valid creation, terminal states, invariants."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import (
    TERMINAL_STATUSES,
    ActionType,
    CaseStatus,
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    StopReason,
    WorkflowType,
    utcnow,
)


def test_valid_case(valid_case: RecoveryCase):
    assert valid_case.case_type is WorkflowType.FAILED_SUBSCRIPTION
    assert valid_case.status is CaseStatus.DETECTED
    assert valid_case.is_terminal is False
    assert valid_case.amount_recovered == Decimal("0")
    assert valid_case.next_action is ActionType.RETRY_PAYMENT


def test_terminal_status_set_correct():
    assert TERMINAL_STATUSES == {
        CaseStatus.RECOVERED,
        CaseStatus.ESCALATED,
        CaseStatus.STOPPED,
        CaseStatus.FAILED,
    }
    for s in TERMINAL_STATUSES:
        assert s.is_terminal is True
    for s in (
        CaseStatus.DETECTED,
        CaseStatus.ANALYZING,
        CaseStatus.ACTION_PENDING,
        CaseStatus.ACTION_APPROVED,
        CaseStatus.ACTION_EXECUTING,
        CaseStatus.WAITING,
    ):
        assert s.is_terminal is False


def test_recovered_case_representation(valid_signal: RevenueRiskSignal):
    case = RecoveryCase(
        case_type=WorkflowType.FAILED_SUBSCRIPTION,
        signal=valid_signal,
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        status=CaseStatus.RECOVERED,
        amount_recovered=Decimal("1500.00"),
    )
    assert case.is_terminal is True
    assert case.status is CaseStatus.RECOVERED


def test_recovered_requires_positive_amount(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        RecoveryCase(
            case_type=WorkflowType.FAILED_SUBSCRIPTION,
            signal=valid_signal,
            amount_at_risk=Decimal("1500.00"),
            currency=Currency.INR,
            status=CaseStatus.RECOVERED,
            amount_recovered=Decimal("0"),
        )


def test_escalated_requires_reason_and_timestamp(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        RecoveryCase(
            case_type=WorkflowType.OVERDUE_RECEIVABLE,
            signal=valid_signal,
            amount_at_risk=Decimal("1500.00"),
            currency=Currency.INR,
            status=CaseStatus.ESCALATED,  # missing escalated_at + reason
        )


def test_escalated_valid(valid_signal: RevenueRiskSignal):
    case = RecoveryCase(
        case_type=WorkflowType.OVERDUE_RECEIVABLE,
        signal=valid_signal,
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        status=CaseStatus.ESCALATED,
        escalated_at=utcnow(),
        escalation_reason="amount over threshold",
    )
    assert case.is_terminal is True


def test_stopped_requires_reason(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        RecoveryCase(
            case_type=WorkflowType.CHECKOUT_ABANDONMENT,
            signal=valid_signal,
            amount_at_risk=Decimal("1500.00"),
            currency=Currency.INR,
            status=CaseStatus.STOPPED,  # missing stop_reason
        )


def test_stopped_valid(valid_signal: RevenueRiskSignal):
    case = RecoveryCase(
        case_type=WorkflowType.CHECKOUT_ABANDONMENT,
        signal=valid_signal,
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        status=CaseStatus.STOPPED,
        stop_reason=StopReason.MAX_ATTEMPTS_REACHED,
    )
    assert case.stop_reason is StopReason.MAX_ATTEMPTS_REACHED


def test_recovered_cannot_exceed_at_risk(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        RecoveryCase(
            case_type=WorkflowType.FAILED_SUBSCRIPTION,
            signal=valid_signal,
            amount_at_risk=Decimal("1000.00"),
            currency=Currency.INR,
            amount_recovered=Decimal("1500.00"),
        )


def test_negative_attempt_count_rejected(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        RecoveryCase(
            case_type=WorkflowType.FAILED_SUBSCRIPTION,
            signal=valid_signal,
            amount_at_risk=Decimal("1000.00"),
            currency=Currency.INR,
            attempt_count=-1,
        )


def test_case_is_mutable_but_validates_on_assignment(valid_case: RecoveryCase):
    # Mutable (unlike frozen events/proposals) but assignment re-validates invariants.
    valid_case.attempt_count = 2
    assert valid_case.attempt_count == 2
    with pytest.raises(ValidationError):
        valid_case.amount_recovered = Decimal("999999.00")  # exceeds at-risk


def test_invalid_status_rejected(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        RecoveryCase(
            case_type=WorkflowType.FAILED_SUBSCRIPTION,
            signal=valid_signal,
            amount_at_risk=Decimal("1000.00"),
            currency=Currency.INR,
            status="halfway_done",
        )


def test_case_roundtrip(valid_case: RecoveryCase):
    restored = RecoveryCase.model_validate_json(valid_case.model_dump_json())
    assert restored.case_id == valid_case.case_id
    assert restored.signal == valid_case.signal
    assert isinstance(restored.amount_at_risk, Decimal)
