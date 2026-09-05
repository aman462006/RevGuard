"""ActionType + ActionProposal: whitelist enforcement and AI-safety boundary."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import (
    EXECUTABLE_ACTIONS,
    RECOMMENDATION_ACTIONS,
    ActionProposal,
    ActionType,
    Currency,
)


def test_valid_proposal():
    p = ActionProposal(
        case_id="case_1",
        action_type=ActionType.RETRY_PAYMENT,
        rationale="First retry after soft decline",
        confidence=0.75,
        parameters={"retry_number": 1},
    )
    assert p.action_type is ActionType.RETRY_PAYMENT
    assert p.proposal_id.startswith("prop_")
    assert p.confidence == 0.75


def test_unlisted_action_type_rejected():
    with pytest.raises(ValidationError):
        ActionProposal(
            case_id="case_1",
            action_type="delete_all_customers",
            rationale="nope",
            confidence=0.5,
        )


def test_action_type_whitelist_is_finite():
    expected = {
        "retry_payment",
        "create_payment_link",
        "send_reminder",
        "record_promise_to_pay",
        "wait",
        "no_action",
        "recommend_escalation",
        "recommend_stop",
    }
    assert {a.value for a in ActionType} == expected


def test_recommendation_vs_executable_partition():
    # Escalate/stop are recommendations only — never executable actions.
    assert ActionType.RECOMMEND_ESCALATION in RECOMMENDATION_ACTIONS
    assert ActionType.RECOMMEND_STOP in RECOMMENDATION_ACTIONS
    assert ActionType.RECOMMEND_ESCALATION not in EXECUTABLE_ACTIONS
    assert ActionType.RECOMMEND_STOP not in EXECUTABLE_ACTIONS
    assert ActionType.RETRY_PAYMENT in EXECUTABLE_ACTIONS


def test_proposal_cannot_represent_arbitrary_external_operation():
    # No fields exist for URLs, endpoints, tool names, SQL, code, or credentials.
    for forbidden in ("url", "endpoint", "tool", "tool_name", "sql", "code", "api_key",
                      "credentials", "function", "command"):
        with pytest.raises(ValidationError):
            ActionProposal(
                case_id="case_1",
                action_type=ActionType.SEND_REMINDER,
                rationale="x",
                confidence=0.5,
                **{forbidden: "http://evil.example/pwn"},
            )


def test_parameters_reject_nested_structures():
    # Scalar-only parameters — a nested dict/list cannot smuggle an operation.
    with pytest.raises(ValidationError):
        ActionProposal(
            case_id="case_1",
            action_type=ActionType.SEND_REMINDER,
            rationale="x",
            confidence=0.5,
            parameters={"payload": {"nested": "object"}},
        )


def test_proposal_has_no_approval_field():
    # A proposal is a recommendation, not an authorization.
    fields = set(ActionProposal.model_fields)
    assert "approved" not in fields
    assert "decision" not in fields
    assert "authorized" not in fields


def test_rationale_required_nonempty():
    with pytest.raises(ValidationError):
        ActionProposal(
            case_id="case_1",
            action_type=ActionType.WAIT,
            rationale="",
            confidence=0.5,
        )


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        ActionProposal(case_id="c", action_type=ActionType.WAIT, rationale="x", confidence=-0.1)
    with pytest.raises(ValidationError):
        ActionProposal(case_id="c", action_type=ActionType.WAIT, rationale="x", confidence=1.1)


def test_expected_amount_requires_currency():
    with pytest.raises(ValidationError):
        ActionProposal(
            case_id="c",
            action_type=ActionType.RETRY_PAYMENT,
            rationale="x",
            confidence=0.5,
            expected_recovery_amount=Decimal("100.00"),
        )


def test_proposal_is_frozen():
    p = ActionProposal(
        case_id="c", action_type=ActionType.WAIT, rationale="x", confidence=0.5
    )
    with pytest.raises(ValidationError):
        p.confidence = 0.9


def test_proposal_roundtrip():
    p = ActionProposal(
        case_id="c",
        action_type=ActionType.CREATE_PAYMENT_LINK,
        rationale="link",
        confidence=0.6,
        expected_recovery_amount=Decimal("250.00"),
        expected_recovery_currency=Currency.INR,
    )
    restored = ActionProposal.model_validate_json(p.model_dump_json())
    assert restored == p
    assert isinstance(restored.expected_recovery_amount, Decimal)
