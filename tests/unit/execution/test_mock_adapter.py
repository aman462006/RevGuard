"""MockAdapter: deterministic simulated outcomes, whitelist-only dispatch."""

from __future__ import annotations

import pytest

from revguard.domain import ActionType, WorkflowType
from revguard.execution import MockAdapter, UnsupportedActionError


def test_all_permitted_actions_produce_simulated_success(make_case, make_proposal):
    case = make_case()
    adapter = MockAdapter()
    for action in (
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECORD_PROMISE_TO_PAY,
        ActionType.WAIT,
    ):
        result = adapter.perform(action, case, make_proposal(case, action))
        assert result.accepted is True
        assert result.succeeded is True
        # Never claims real money — always flagged simulated.
        assert result.simulated is True


def test_results_are_deterministic(make_case, make_proposal):
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
    first = MockAdapter().perform(ActionType.RETRY_PAYMENT, case, proposal)
    second = MockAdapter().perform(ActionType.RETRY_PAYMENT, case, proposal)
    # Same case + proposal → identical AdapterResult (incl. the derived reference).
    assert first == second
    assert first.reference is not None


def test_wait_has_no_provider_reference(make_case, make_proposal):
    case = make_case(WorkflowType.PAYMENT_DEGRADATION)
    result = MockAdapter().perform(ActionType.WAIT, case, make_proposal(case, ActionType.WAIT))
    assert result.succeeded is True
    assert result.reference is None


def test_fail_actions_produce_deterministic_failure(make_case, make_proposal):
    case = make_case()
    adapter = MockAdapter(fail_actions=frozenset({ActionType.RETRY_PAYMENT}))
    result = adapter.perform(ActionType.RETRY_PAYMENT, case, make_proposal(case))
    assert result.accepted is True
    assert result.succeeded is False
    assert result.failure_reason is not None


def test_non_whitelisted_action_is_rejected(make_case, make_proposal):
    case = make_case()
    adapter = MockAdapter()
    with pytest.raises(UnsupportedActionError):
        adapter.perform(
            ActionType.RECOMMEND_ESCALATION,
            case,
            make_proposal(case, ActionType.RETRY_PAYMENT),
        )
    # A rejected dispatch never runs a handler.
    assert adapter.calls == []
