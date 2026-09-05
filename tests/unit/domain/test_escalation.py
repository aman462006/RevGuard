"""EscalationRecord: required fields, ESCALATE-only, resolvable by a human."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import (
    ActionType,
    Currency,
    DecisionType,
    EscalationRecord,
    EscalationStatus,
    utcnow,
)


def _valid_record(**overrides) -> EscalationRecord:
    base = dict(
        case_id="case_1",
        reason="amount over escalation threshold",
        amount_at_risk=Decimal("50000.00"),
        currency=Currency.INR,
        customer_id="cust_1",
        ai_recommended_action=ActionType.RECOMMEND_ESCALATION,
        ai_rationale="high value, low confidence",
    )
    base.update(overrides)
    return EscalationRecord(**base)


def test_valid_escalation_record():
    r = _valid_record()
    assert r.status is EscalationStatus.OPEN
    assert r.policy_decision is DecisionType.ESCALATE
    assert r.escalation_id.startswith("esc_")
    # Contains the minimum required fields per PRODUCT_SPEC §6.
    for field in ("case_id", "reason", "amount_at_risk", "customer_id",
                  "ai_recommended_action", "policy_decision", "escalated_at", "status"):
        assert field in EscalationRecord.model_fields


def test_policy_decision_must_be_escalate():
    with pytest.raises(ValidationError):
        _valid_record(policy_decision=DecisionType.APPROVE)


def test_reason_required():
    with pytest.raises(ValidationError):
        _valid_record(reason="")


def test_amount_must_be_positive():
    with pytest.raises(ValidationError):
        _valid_record(amount_at_risk=Decimal("0"))


def test_resolution_flow():
    r = _valid_record()
    r.status = EscalationStatus.IN_REVIEW
    # resolved_at must be set before/with the RESOLVED status (invariant enforced on
    # assignment), so a correct resolution records the timestamp first.
    r.resolved_at = utcnow()
    r.resolution_note = "manually retried, customer paid"
    r.status = EscalationStatus.RESOLVED
    assert r.status is EscalationStatus.RESOLVED


def test_resolved_requires_timestamp():
    r = _valid_record()
    with pytest.raises(ValidationError):
        r.status = EscalationStatus.RESOLVED  # resolved_at still None


def test_escalation_roundtrip():
    r = _valid_record()
    assert EscalationRecord.model_validate_json(r.model_dump_json()) == r
