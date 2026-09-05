"""ActionExecutor: gating, idempotency, outcome recording, and safety boundaries."""

from __future__ import annotations

import pytest

from revguard.domain import (
    ActionType,
    DecisionType,
    ExecutionStatus,
    RecoveryOutcome,
    VerificationStatus,
)
from revguard.execution import ActionExecutor, ExecutionRejected, MockAdapter

# -- APPROVE executes ------------------------------------------------------------------


def test_approve_executes_action(make_case, make_proposal, make_decision):
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
    decision = make_decision(case, proposal, DecisionType.APPROVE)
    adapter = MockAdapter()

    record = ActionExecutor(adapter).execute(decision, proposal, case)

    assert record.action is ActionType.RETRY_PAYMENT
    assert record.result.execution_status is ExecutionStatus.SUCCEEDED
    assert adapter.calls == [(ActionType.RETRY_PAYMENT, case.case_id)]
    assert record.from_cache is False


# -- ESCALATE / STOP do not execute ----------------------------------------------------


@pytest.mark.parametrize("blocked", [DecisionType.ESCALATE, DecisionType.STOP])
def test_non_approve_decision_does_not_execute(make_case, make_proposal, make_decision, blocked):
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
    decision = make_decision(case, proposal, blocked)
    adapter = MockAdapter()

    with pytest.raises(ExecutionRejected):
        ActionExecutor(adapter).execute(decision, proposal, case)

    # The adapter was never invoked — no side effect.
    assert adapter.calls == []


# -- unknown / unpermitted action cannot execute ---------------------------------------


def test_mismatched_action_is_rejected(make_case, make_proposal, make_decision):
    """An APPROVE that does not reference the exact proposed action is refused."""
    case = make_case()
    proposal = make_proposal(case, ActionType.SEND_REMINDER)
    # Decision approves a *different* action than the proposal carries.
    decision = make_decision(case, proposal, DecisionType.APPROVE)
    decision = decision.model_copy(update={"proposed_action": ActionType.RETRY_PAYMENT})
    adapter = MockAdapter()

    with pytest.raises(ExecutionRejected):
        ActionExecutor(adapter).execute(decision, proposal, case)
    assert adapter.calls == []


# -- idempotency -----------------------------------------------------------------------


def test_duplicate_execution_is_idempotent(make_case, make_proposal, make_decision):
    case = make_case()
    proposal = make_proposal(
        case, ActionType.RETRY_PAYMENT, parameters={"idempotency_key": "idem-1"}
    )
    decision = make_decision(case, proposal, DecisionType.APPROVE)
    adapter = MockAdapter()
    executor = ActionExecutor(adapter)

    first = executor.execute(decision, proposal, case)
    second = executor.execute(decision, proposal, case)

    # Adapter ran exactly once; the second call is served from the idempotency cache.
    assert len(adapter.calls) == 1
    assert second.from_cache is True
    assert second.result is first.result


def test_same_case_action_cannot_execute_twice_without_explicit_key(
    make_case, make_proposal, make_decision
):
    """Even without a supplied key, the derived key blocks an accidental re-run."""
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)  # no idempotency_key param
    decision = make_decision(case, proposal, DecisionType.APPROVE)
    adapter = MockAdapter()
    executor = ActionExecutor(adapter)

    executor.execute(decision, proposal, case)
    executor.execute(decision, proposal, case)
    assert len(adapter.calls) == 1


def test_different_cases_remain_independent(make_case, make_proposal, make_decision):
    case_a = make_case()
    case_b = make_case()
    assert case_a.case_id != case_b.case_id
    adapter = MockAdapter()
    executor = ActionExecutor(adapter)

    for case in (case_a, case_b):
        proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
        decision = make_decision(case, proposal, DecisionType.APPROVE)
        executor.execute(decision, proposal, case)

    # Independent keys → both executed.
    assert len(adapter.calls) == 2
    assert {c for _, c in adapter.calls} == {case_a.case_id, case_b.case_id}


# -- failure representation ------------------------------------------------------------


def test_failed_execution_is_represented_correctly(make_case, make_proposal, make_decision):
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
    decision = make_decision(case, proposal, DecisionType.APPROVE)
    adapter = MockAdapter(fail_actions=frozenset({ActionType.RETRY_PAYMENT}))

    record = ActionExecutor(adapter).execute(decision, proposal, case)

    assert record.result.execution_status is ExecutionStatus.FAILED
    assert record.result.outcome is RecoveryOutcome.ACTION_FAILED
    assert record.result.failure_reason is not None
    # A failed action recovered nothing.
    assert record.result.amount_recovered == 0
    assert record.result.is_recovered is False


# -- execution success ≠ money recovered -----------------------------------------------


def test_successful_execution_does_not_mean_money_recovered(
    make_case, make_proposal, make_decision
):
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
    decision = make_decision(case, proposal, DecisionType.APPROVE)

    record = ActionExecutor(MockAdapter()).execute(decision, proposal, case)

    # Technically succeeded...
    assert record.result.execution_status is ExecutionStatus.SUCCEEDED
    assert record.adapter_result.succeeded is True
    # ...but recovery is only PENDING verification (Phase 7), never asserted here.
    assert record.result.verification_status is VerificationStatus.PENDING
    assert record.result.is_recovered is False
    assert record.result.amount_recovered == 0
    # The simulated outcome is explicitly flagged as not-real.
    assert record.adapter_result.simulated is True


# -- audit integration -----------------------------------------------------------------


def test_execution_records_audit_event(make_case, make_proposal, make_decision, audit_sink):
    case = make_case()
    proposal = make_proposal(case, ActionType.SEND_REMINDER)
    decision = make_decision(case, proposal, DecisionType.APPROVE)

    ActionExecutor(MockAdapter(), audit=audit_sink).execute(decision, proposal, case)

    assert len(audit_sink.events) == 1
    event = audit_sink.events[0]
    assert event["action"] == ActionType.SEND_REMINDER.value
    assert event["details"]["simulated"] is True
    assert event["details"]["execution_status"] == ExecutionStatus.SUCCEEDED.value


def test_rejected_execution_is_audited(make_case, make_proposal, make_decision, audit_sink):
    case = make_case()
    proposal = make_proposal(case, ActionType.RETRY_PAYMENT)
    decision = make_decision(case, proposal, DecisionType.STOP)

    with pytest.raises(ExecutionRejected):
        ActionExecutor(MockAdapter(), audit=audit_sink).execute(decision, proposal, case)

    assert len(audit_sink.events) == 1
    assert audit_sink.events[0]["details"]["rejected"] is True
