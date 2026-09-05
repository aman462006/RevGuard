"""Safety boundaries: the executor is bound to PolicyEngine and free of any LLM/arbitrary
execution."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

import revguard.execution as execution_pkg
from revguard.diagnosis import MockDiagnoser
from revguard.domain import DecisionType, WorkflowType
from revguard.execution import ActionExecutor, ExecutionRejected, MockAdapter
from revguard.policy import PolicyEngine


def test_executor_runs_only_what_the_engine_approves(make_case):
    """End-to-end: an APPROVE from the real engine executes."""
    case = make_case(WorkflowType.FAILED_SUBSCRIPTION, amount=Decimal("1500.00"))
    proposal = MockDiagnoser().diagnose(case)
    decision = PolicyEngine().evaluate(proposal, case)
    assert decision.decision is DecisionType.APPROVE

    adapter = MockAdapter()
    record = ActionExecutor(adapter).execute(decision, proposal, case)
    assert record.result.action is proposal.action_type
    assert len(adapter.calls) == 1


def test_executor_cannot_bypass_policy_engine(make_case):
    """When the real engine ESCALATEs an over-threshold case, the executor refuses to run."""
    case = make_case(WorkflowType.FAILED_SUBSCRIPTION, amount=Decimal("90000.00"))
    proposal = MockDiagnoser().diagnose(case)
    decision = PolicyEngine().evaluate(proposal, case)
    assert decision.decision is DecisionType.ESCALATE  # amount over threshold

    adapter = MockAdapter()
    with pytest.raises(ExecutionRejected):
        ActionExecutor(adapter).execute(decision, proposal, case)
    assert adapter.calls == []


def test_execution_layer_has_no_llm_or_arbitrary_execution():
    """The executor must not import an LLM/diagnosis layer or use dynamic code execution."""
    forbidden = (
        "revguard.diagnosis",
        "anthropic",
        "import openai",
        "eval(",
        "__import__",
    )
    pkg_dir = Path(execution_pkg.__file__).parent
    for path in pkg_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} must not reference {token!r}"
