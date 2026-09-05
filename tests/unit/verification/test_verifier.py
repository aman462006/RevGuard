"""MockVerifier: it is the sole authority on recovery and simulates every outcome."""

from __future__ import annotations

from decimal import Decimal

from revguard.domain import (
    ActionProposal,
    ActionType,
    Currency,
    DecisionType,
    ExecutionStatus,
    PolicyDecision,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    VerificationStatus,
    WorkflowType,
)
from revguard.execution import ActionExecutor, ExecutionRecord, MockAdapter
from revguard.verification import MockVerifier


def _case(amount: Decimal = Decimal("1500.00")) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.FAILED_SUBSCRIPTION,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        amount_at_risk=amount,
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    return RecoveryCase(
        case_type=WorkflowType.FAILED_SUBSCRIPTION,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=amount,
        currency=Currency.INR,
    )


def _execution(
    case: RecoveryCase,
    action: ActionType = ActionType.RETRY_PAYMENT,
    *,
    fail: bool = False,
) -> ExecutionRecord:
    proposal = ActionProposal(
        case_id=case.case_id, action_type=action, rationale="x", confidence=0.8
    )
    decision = PolicyDecision(
        case_id=case.case_id,
        decision=DecisionType.APPROVE,
        proposed_action=action,
        reason="ok",
        matched_rules=["approve.permitted_action"],
    )
    fail_actions = frozenset({action}) if fail else frozenset()
    return ActionExecutor(MockAdapter(fail_actions=fail_actions)).execute(
        decision, proposal, case
    )


def test_recovered_reports_amount_and_reference():
    case = _case()
    proposal = ActionProposal(
        case_id=case.case_id,
        action_type=ActionType.RETRY_PAYMENT,
        rationale="x",
        confidence=0.8,
        expected_recovery_amount=Decimal("1500.00"),
        expected_recovery_currency=Currency.INR,
    )
    result = MockVerifier(default=VerificationStatus.RECOVERED).verify(
        case, proposal, _execution(case)
    )
    assert result.is_recovered is True
    assert result.amount_recovered == Decimal("1500.00")
    assert result.currency is Currency.INR
    assert result.payment_reference is not None


def test_pending_recovers_nothing():
    case = _case()
    proposal = ActionProposal(
        case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT, rationale="x", confidence=0.8
    )
    result = MockVerifier(default=VerificationStatus.PENDING).verify(
        case, proposal, _execution(case)
    )
    assert result.verification_status is VerificationStatus.PENDING
    assert result.is_recovered is False
    assert result.amount_recovered == 0


def test_not_recovered_recovers_nothing():
    case = _case()
    proposal = ActionProposal(
        case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT, rationale="x", confidence=0.8
    )
    result = MockVerifier(default=VerificationStatus.NOT_RECOVERED).verify(
        case, proposal, _execution(case)
    )
    assert result.verification_status is VerificationStatus.NOT_RECOVERED
    assert result.amount_recovered == 0


def test_failed_execution_never_becomes_recovered():
    """Even if the verifier is told RECOVERED, a failed action recovers nothing."""
    case = _case()
    proposal = ActionProposal(
        case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT, rationale="x", confidence=0.8
    )
    execution = _execution(case, fail=True)
    result = MockVerifier(default=VerificationStatus.RECOVERED).verify(case, proposal, execution)
    assert result.is_recovered is False
    assert result.execution_status is ExecutionStatus.FAILED
    assert result.amount_recovered == 0


def test_wait_is_always_pending():
    case = _case()
    proposal = ActionProposal(
        case_id=case.case_id, action_type=ActionType.WAIT, rationale="x", confidence=0.55
    )
    result = MockVerifier(default=VerificationStatus.NOT_RECOVERED).verify(
        case, proposal, _execution(case, ActionType.WAIT)
    )
    assert result.verification_status is VerificationStatus.PENDING


def test_recovered_amount_is_verifier_controlled():
    case = _case(Decimal("1500.00"))
    proposal = ActionProposal(
        case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT, rationale="x", confidence=0.8
    )
    result = MockVerifier(
        default=VerificationStatus.RECOVERED, recovered_amount=Decimal("750.00")
    ).verify(case, proposal, _execution(case))
    assert result.amount_recovered == Decimal("750.00")


def test_sequence_is_consumed_per_call():
    case = _case()
    proposal = ActionProposal(
        case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT, rationale="x", confidence=0.8
    )
    verifier = MockVerifier(
        sequence=[VerificationStatus.NOT_RECOVERED, VerificationStatus.RECOVERED]
    )
    first = verifier.verify(case, proposal, _execution(case))
    second = verifier.verify(case, proposal, _execution(case))
    assert first.is_recovered is False
    assert second.is_recovered is True


def test_execution_result_is_never_recovered_on_its_own():
    """The executor's own result is only ever PENDING — recovery comes from the verifier."""
    case = _case()
    execution = _execution(case)
    assert execution.result.verification_status is VerificationStatus.PENDING
    assert execution.result.amount_recovered == 0
