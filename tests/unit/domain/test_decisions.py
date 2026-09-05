"""PolicyDecision + DecisionType: only APPROVE/ESCALATE/STOP; APPROVE gates execution."""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import (
    ActionType,
    Currency,  # noqa: F401  (kept for parity with other suites)
    DecisionType,
    PolicyDecision,
)


def test_valid_approve_decision():
    d = PolicyDecision(
        case_id="case_1",
        decision=DecisionType.APPROVE,
        proposed_action=ActionType.RETRY_PAYMENT,
        reason="within limits",
        matched_rules=["max_attempts.ok", "amount.under_cap"],
    )
    assert d.decision is DecisionType.APPROVE
    assert d.decision_id.startswith("dec_")


def test_only_three_decision_types_exist():
    assert {d.value for d in DecisionType} == {"approve", "escalate", "stop"}


def test_invalid_decision_type_rejected():
    with pytest.raises(ValidationError):
        PolicyDecision(
            case_id="c",
            decision="maybe",
            reason="x",
            matched_rules=["r"],
        )


def test_approve_requires_proposed_action():
    with pytest.raises(ValidationError):
        PolicyDecision(
            case_id="c",
            decision=DecisionType.APPROVE,
            reason="x",
            matched_rules=["r"],
        )


def test_approve_cannot_sanction_a_recommendation():
    # RECOMMEND_* are not executable, so APPROVE may not sanction them.
    with pytest.raises(ValidationError):
        PolicyDecision(
            case_id="c",
            decision=DecisionType.APPROVE,
            proposed_action=ActionType.RECOMMEND_ESCALATION,
            reason="x",
            matched_rules=["r"],
        )


def test_escalate_and_stop_need_no_action():
    esc = PolicyDecision(
        case_id="c",
        decision=DecisionType.ESCALATE,
        reason="over threshold",
        matched_rules=["escalation.threshold"],
        amount_at_risk=Decimal("50000.00"),
        threshold_amount=Decimal("25000.00"),
    )
    stop = PolicyDecision(
        case_id="c",
        decision=DecisionType.STOP,
        reason="already recovered",
        matched_rules=["already_recovered"],
    )
    assert esc.decision is DecisionType.ESCALATE
    assert stop.decision is DecisionType.STOP


def test_reason_required():
    with pytest.raises(ValidationError):
        PolicyDecision(
            case_id="c",
            decision=DecisionType.STOP,
            reason="",
            matched_rules=["r"],
        )


def test_matched_rules_required():
    with pytest.raises(ValidationError):
        PolicyDecision(
            case_id="c",
            decision=DecisionType.STOP,
            reason="x",
            matched_rules=[],
        )


def test_decision_is_frozen():
    d = PolicyDecision(
        case_id="c",
        decision=DecisionType.STOP,
        reason="x",
        matched_rules=["r"],
    )
    with pytest.raises(ValidationError):
        d.reason = "changed"


def test_decision_roundtrip():
    d = PolicyDecision(
        case_id="c",
        decision=DecisionType.APPROVE,
        proposed_action=ActionType.SEND_REMINDER,
        reason="ok",
        matched_rules=["r"],
        attempt_count=1,
        max_attempts=3,
    )
    assert PolicyDecision.model_validate_json(d.model_dump_json()) == d
