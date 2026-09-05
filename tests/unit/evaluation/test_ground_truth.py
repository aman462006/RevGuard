"""Offline ground truth + GroundTruthVerifier: deterministic, seed-driven recovery."""

from __future__ import annotations

from decimal import Decimal

from revguard.domain import (
    ActionProposal,
    ActionType,
    Currency,
    DecisionType,
    PolicyDecision,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    VerificationStatus,
    WorkflowType,
)
from revguard.evaluation.ground_truth import GroundTruthVerifier, ground_truth_for
from revguard.execution import ActionExecutor, MockAdapter


def _case(
    *,
    case_id: str = "case_eval_1",
    amount: Decimal = Decimal("1000.00"),
    attempt_count: int = 0,
) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.FAILED_SUBSCRIPTION,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        amount_at_risk=amount,
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    return RecoveryCase(
        case_id=case_id,
        case_type=WorkflowType.FAILED_SUBSCRIPTION,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=amount,
        currency=Currency.INR,
        attempt_count=attempt_count,
    )


def _execution(case: RecoveryCase, action: ActionType, *, fail: bool = False):
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


def test_ground_truth_is_deterministic_for_a_seed():
    case = _case()
    a = ground_truth_for(case, 42)
    b = ground_truth_for(case, 42)
    assert a == b
    assert 1 <= a.recover_at_attempt <= 3
    assert a.recoverable_amount == case.amount_at_risk


def test_ground_truth_varies_with_seed():
    case = _case()
    truths = {
        (ground_truth_for(case, s).recoverable, ground_truth_for(case, s).recover_at_attempt)
        for s in range(30)
    }
    # Across many seeds we see more than one distinct outcome (it is not a constant).
    assert len(truths) > 1


def _recoverable_seed(case: RecoveryCase, *, min_attempt: int = 1) -> int:
    for seed in range(500):
        truth = ground_truth_for(case, seed)
        if truth.recoverable and truth.recover_at_attempt >= min_attempt:
            return seed
    raise AssertionError("no recoverable seed found")


def test_recovering_action_recovers_when_enough_attempts():
    probe = _case()
    seed = _recoverable_seed(probe)
    truth = ground_truth_for(probe, seed)
    case = _case(attempt_count=truth.recover_at_attempt)  # exactly enough attempts

    result = GroundTruthVerifier(seed=seed).verify(
        case,
        ActionProposal(case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT,
                       rationale="x", confidence=0.8),
        _execution(case, ActionType.RETRY_PAYMENT),
    )
    assert result.is_recovered is True
    assert result.amount_recovered == case.amount_at_risk
    assert result.payment_reference is not None


def test_recovering_action_not_recovered_before_threshold():
    probe = _case()
    seed = _recoverable_seed(probe, min_attempt=2)
    truth = ground_truth_for(probe, seed)
    case = _case(attempt_count=truth.recover_at_attempt - 1)  # one short

    result = GroundTruthVerifier(seed=seed).verify(
        case,
        ActionProposal(case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT,
                       rationale="x", confidence=0.8),
        _execution(case, ActionType.RETRY_PAYMENT),
    )
    assert result.is_recovered is False
    assert result.amount_recovered == Decimal("0")


def test_wait_action_is_pending():
    probe = _case()
    seed = _recoverable_seed(probe)
    truth = ground_truth_for(probe, seed)
    case = _case(attempt_count=truth.recover_at_attempt)  # enough attempts, but WAIT

    result = GroundTruthVerifier(seed=seed).verify(
        case,
        ActionProposal(case_id=case.case_id, action_type=ActionType.WAIT,
                       rationale="x", confidence=0.55),
        _execution(case, ActionType.WAIT),
    )
    assert result.verification_status is VerificationStatus.PENDING
    assert result.amount_recovered == Decimal("0")


def test_failed_execution_never_recovers():
    probe = _case()
    seed = _recoverable_seed(probe)
    truth = ground_truth_for(probe, seed)
    case = _case(attempt_count=truth.recover_at_attempt)

    result = GroundTruthVerifier(seed=seed).verify(
        case,
        ActionProposal(case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT,
                       rationale="x", confidence=0.8),
        _execution(case, ActionType.RETRY_PAYMENT, fail=True),
    )
    assert result.is_recovered is False


def test_unrecoverable_case_is_never_recovered():
    case = _case(attempt_count=3)
    # Find a seed where the case is NOT recoverable.
    seed = next(s for s in range(500) if not ground_truth_for(case, s).recoverable)
    result = GroundTruthVerifier(seed=seed).verify(
        case,
        ActionProposal(case_id=case.case_id, action_type=ActionType.RETRY_PAYMENT,
                       rationale="x", confidence=0.8),
        _execution(case, ActionType.RETRY_PAYMENT),
    )
    assert result.is_recovered is False
    assert result.verification_status is VerificationStatus.NOT_RECOVERED
