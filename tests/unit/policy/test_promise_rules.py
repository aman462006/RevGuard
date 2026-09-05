"""Deterministic promise-to-pay authorization through the PolicyEngine.

Recording a promise is authorized (or refused) only by the deterministic engine: a first
promise on an eligible overdue case is approved, a duplicate while one is still open is
refused (escalated), a closed promise no longer blocks a new one, and a promise on a terminal
case is stopped by the existing terminal rule.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from revguard.domain import (
    Currency,
    DecisionType,
    PromiseStatus,
    PromiseToPay,
    StopReason,
    WorkflowType,
    utcnow,
)
from revguard.policy import PolicyEngine
from revguard.policy.context import RuleId

_PTP = "record_promise_to_pay"


def _promise(status: PromiseStatus) -> PromiseToPay:
    return PromiseToPay(
        case_id="c",
        promised_at=utcnow() + timedelta(days=2),
        status=status,
        amount=Decimal("5000.00"),
        currency=Currency.INR,
    )


def _overdue(make_case, **overrides):
    return make_case(case_type=WorkflowType.OVERDUE_RECEIVABLE, **overrides)


def test_first_promise_is_approved(make_case, make_proposal):
    case = _overdue(make_case)  # no existing promise
    decision = PolicyEngine().evaluate(
        make_proposal(case, action="record_promise_to_pay"), case
    )
    assert decision.decision is DecisionType.APPROVE


def test_duplicate_open_promise_is_escalated(make_case, make_proposal):
    for status in (PromiseStatus.PROMISED, PromiseStatus.PENDING):
        case = _overdue(make_case, promise=_promise(status))
        decision = PolicyEngine().evaluate(
            make_proposal(case, action="record_promise_to_pay"), case
        )
        assert decision.decision is DecisionType.ESCALATE
        assert decision.matched_rules == [RuleId.ESCALATE_DUPLICATE_PROMISE.value]


def test_closed_promise_does_not_block_a_new_one(make_case, make_proposal):
    # A kept/missed promise is resolved, so recording a fresh promise is allowed again.
    for status in (PromiseStatus.KEPT, PromiseStatus.MISSED):
        case = _overdue(make_case, promise=_promise(status))
        decision = PolicyEngine().evaluate(
            make_proposal(case, action="record_promise_to_pay"), case
        )
        assert decision.decision is DecisionType.APPROVE


def test_promise_on_terminal_case_is_stopped(make_case, make_proposal):
    case = _overdue(
        make_case, status="stopped", stopped_at=utcnow(), stop_reason=StopReason.MANUAL_STOP
    )
    decision = PolicyEngine().evaluate(
        make_proposal(case, action="record_promise_to_pay"), case
    )
    assert decision.decision is DecisionType.STOP
    assert decision.matched_rules == [RuleId.STOP_TERMINAL_CASE.value]


def test_duplicate_promise_never_bypassed_by_confidence(make_case, make_proposal):
    # Even a maximally confident proposal cannot record a duplicate promise.
    case = _overdue(make_case, promise=_promise(PromiseStatus.PROMISED))
    decision = PolicyEngine().evaluate(
        make_proposal(case, action="record_promise_to_pay", confidence=1.0), case
    )
    assert decision.decision is DecisionType.ESCALATE
