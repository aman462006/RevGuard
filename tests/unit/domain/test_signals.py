"""RevenueRiskSignal: valid creation, evidence, source traceability, no-action property."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import (
    Currency,
    DataProvenance,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


def test_valid_signal(valid_signal: RevenueRiskSignal):
    assert valid_signal.signal_type is WorkflowType.FAILED_SUBSCRIPTION
    assert valid_signal.risk_level is RiskLevel.HIGH
    assert valid_signal.amount_at_risk == Decimal("1500.00")
    assert valid_signal.signal_id.startswith("sig_")
    assert valid_signal.confidence == 0.9


def test_signal_provenance_defaults_to_internal_not_synthetic():
    # A signal is NEVER synthetic by default — provenance must be set explicitly (by the
    # DetectionEngine) so real/unknown data can never be mislabelled as demo data.
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.OVERDUE_RECEIVABLE,
        risk_level=RiskLevel.HIGH,
        amount_at_risk=Decimal("100.00"),
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    assert signal.provenance is DataProvenance.INTERNAL
    assert signal.provenance.is_synthetic is False


def test_signal_requires_source_event():
    with pytest.raises(ValidationError):
        RevenueRiskSignal(
            signal_type=WorkflowType.CHECKOUT_ABANDONMENT,
            risk_level=RiskLevel.LOW,
            amount_at_risk=Decimal("100.00"),
            currency=Currency.INR,
            source_event_ids=[],  # empty not allowed
        )


def test_signal_rejects_duplicate_source_events():
    with pytest.raises(ValidationError):
        RevenueRiskSignal(
            signal_type=WorkflowType.CHECKOUT_ABANDONMENT,
            risk_level=RiskLevel.LOW,
            amount_at_risk=Decimal("100.00"),
            currency=Currency.INR,
            source_event_ids=["evt_1", "evt_1"],
        )


def test_signal_amount_must_be_positive():
    with pytest.raises(ValidationError):
        RevenueRiskSignal(
            signal_type=WorkflowType.OVERDUE_RECEIVABLE,
            risk_level=RiskLevel.HIGH,
            amount_at_risk=Decimal("0"),
            currency=Currency.INR,
            source_event_ids=["evt_1"],
        )


def test_confidence_out_of_range_rejected():
    with pytest.raises(ValidationError):
        RevenueRiskSignal(
            signal_type=WorkflowType.OVERDUE_RECEIVABLE,
            risk_level=RiskLevel.HIGH,
            amount_at_risk=Decimal("100.00"),
            currency=Currency.INR,
            source_event_ids=["evt_1"],
            confidence=1.5,
        )


def test_signal_has_no_action_field(valid_signal: RevenueRiskSignal):
    # Detectors conclude risk, never an action. Guard against regression.
    fields = set(type(valid_signal).model_fields)
    assert "action" not in fields
    assert "action_type" not in fields


def test_signal_is_frozen(valid_signal: RevenueRiskSignal):
    with pytest.raises(ValidationError):
        valid_signal.risk_level = RiskLevel.LOW


def test_signal_roundtrip(valid_signal: RevenueRiskSignal):
    restored = RevenueRiskSignal.model_validate_json(valid_signal.model_dump_json())
    assert restored == valid_signal
    assert isinstance(restored.amount_at_risk, Decimal)
