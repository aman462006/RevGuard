"""RecoveryResult: the executed-≠-recovered separation is enforced."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import (
    ActionType,
    Currency,
    ExecutionStatus,
    RecoveryOutcome,
    RecoveryResult,
    VerificationStatus,
)


def test_valid_recovered_result():
    r = RecoveryResult(
        case_id="c",
        action=ActionType.RETRY_PAYMENT,
        execution_status=ExecutionStatus.SUCCEEDED,
        verification_status=VerificationStatus.RECOVERED,
        outcome=RecoveryOutcome.RECOVERED,
        amount_recovered=Decimal("1500.00"),
        currency=Currency.INR,
        payment_reference="pay_test_123",
    )
    assert r.is_recovered is True
    assert r.action_succeeded_technically is True


def test_technical_success_is_not_recovery():
    # Executed successfully, but verification says money was NOT recovered.
    r = RecoveryResult(
        case_id="c",
        action=ActionType.RETRY_PAYMENT,
        execution_status=ExecutionStatus.SUCCEEDED,
        verification_status=VerificationStatus.NOT_RECOVERED,
        outcome=RecoveryOutcome.NOT_RECOVERED,
    )
    assert r.action_succeeded_technically is True
    assert r.is_recovered is False
    assert r.amount_recovered == Decimal("0")


def test_money_recorded_without_verification_is_rejected():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.NOT_RECOVERED,
            outcome=RecoveryOutcome.NOT_RECOVERED,
            amount_recovered=Decimal("1500.00"),  # not allowed without RECOVERED
            currency=Currency.INR,
        )


def test_outcome_recovered_requires_verified_recovery():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.PENDING,
            outcome=RecoveryOutcome.RECOVERED,  # inconsistent with PENDING
        )


def test_recovered_requires_payment_reference():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.RECOVERED,
            outcome=RecoveryOutcome.RECOVERED,
            amount_recovered=Decimal("100.00"),
            currency=Currency.INR,
            # payment_reference missing
        )


def test_recovered_requires_positive_amount():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.RECOVERED,
            outcome=RecoveryOutcome.RECOVERED,
            amount_recovered=Decimal("0"),
            currency=Currency.INR,
            payment_reference="pay_1",
        )


def test_recovered_requires_currency():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.RECOVERED,
            outcome=RecoveryOutcome.RECOVERED,
            amount_recovered=Decimal("100.00"),
            payment_reference="pay_1",
            # currency missing
        )


def test_failed_execution_requires_reason():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.FAILED,
            verification_status=VerificationStatus.NOT_RECOVERED,
            outcome=RecoveryOutcome.ACTION_FAILED,
            # failure_reason missing
        )


def test_action_failed_result_valid():
    r = RecoveryResult(
        case_id="c",
        action=ActionType.RETRY_PAYMENT,
        execution_status=ExecutionStatus.FAILED,
        verification_status=VerificationStatus.NOT_RECOVERED,
        outcome=RecoveryOutcome.ACTION_FAILED,
        failure_reason="gateway timeout",
    )
    assert r.is_recovered is False


def test_no_action_outcome_requires_no_action():
    with pytest.raises(ValidationError):
        RecoveryResult(
            case_id="c",
            action=ActionType.RETRY_PAYMENT,
            execution_status=ExecutionStatus.SKIPPED,
            verification_status=VerificationStatus.UNVERIFIED,
            outcome=RecoveryOutcome.NO_ACTION,
        )


def test_rejected_action_is_action_failed():
    # An executor rejecting a non-APPROVE action is representable as ACTION_FAILED.
    r = RecoveryResult(
        case_id="c",
        action=ActionType.RETRY_PAYMENT,
        execution_status=ExecutionStatus.REJECTED,
        verification_status=VerificationStatus.UNVERIFIED,
        outcome=RecoveryOutcome.ACTION_FAILED,
    )
    assert r.execution_status is ExecutionStatus.REJECTED
    assert r.is_recovered is False


def test_result_is_frozen():
    r = RecoveryResult(
        case_id="c",
        execution_status=ExecutionStatus.SKIPPED,
        verification_status=VerificationStatus.UNVERIFIED,
        outcome=RecoveryOutcome.NO_ACTION,
    )
    with pytest.raises(ValidationError):
        r.outcome = RecoveryOutcome.RECOVERED


def test_result_roundtrip():
    r = RecoveryResult(
        case_id="c",
        action=ActionType.CREATE_PAYMENT_LINK,
        execution_status=ExecutionStatus.SUCCEEDED,
        verification_status=VerificationStatus.RECOVERED,
        outcome=RecoveryOutcome.RECOVERED,
        amount_recovered=Decimal("999.00"),
        currency=Currency.INR,
        payment_reference="pay_9",
    )
    restored = RecoveryResult.model_validate_json(r.model_dump_json())
    assert restored == r
    assert isinstance(restored.amount_recovered, Decimal)
