"""RazorpayVerifier: verified provider status is the sole source of truth for recovery."""

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
from revguard.execution import ActionExecutor, MockAdapter
from revguard.integrations.razorpay.verifier import RazorpayVerifier


class _FetchResource:
    def __init__(self, ret):
        self._ret = ret
        self.fetched: list = []

    def fetch(self, reference):
        self.fetched.append(reference)
        if isinstance(self._ret, Exception):
            raise self._ret
        return self._ret


class _FakeClient:
    def __init__(self, *, order=None, payment_link=None):
        self.order = _FetchResource(order)
        self.payment_link = _FetchResource(payment_link)


def _case() -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.FAILED_SUBSCRIPTION,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    return RecoveryCase(
        case_id="case_x",
        case_type=WorkflowType.FAILED_SUBSCRIPTION,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
    )


def _execution(action: ActionType, adapter: MockAdapter, *, fail: bool = False):
    case = _case()
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
    return ActionExecutor(adapter).execute(decision, proposal, case)


def test_paid_status_confirms_recovery_with_provider_amount():
    client = _FakeClient(payment_link={"id": "plink_1", "status": "paid",
                                       "amount_paid": 150000})
    execution = _execution(ActionType.CREATE_PAYMENT_LINK, MockAdapter())
    result = RazorpayVerifier(client).verify(_case(), _proposal(), execution)
    assert result.is_recovered is True
    assert result.amount_recovered == Decimal("1500.00")
    assert result.payment_reference is not None
    assert client.payment_link.fetched  # it actually consulted Razorpay


def test_created_but_unpaid_link_is_pending_not_recovered():
    # API success (a link was created) is NOT recovery — only a paid status is.
    client = _FakeClient(payment_link={"id": "plink_1", "status": "created"})
    execution = _execution(ActionType.CREATE_PAYMENT_LINK, MockAdapter())
    result = RazorpayVerifier(client).verify(_case(), _proposal(), execution)
    assert result.verification_status is VerificationStatus.PENDING
    assert result.amount_recovered == Decimal("0")


def test_failed_execution_is_not_recovered():
    client = _FakeClient(payment_link={"id": "plink_1", "status": "paid",
                                       "amount_paid": 150000})
    execution = _execution(
        ActionType.CREATE_PAYMENT_LINK, MockAdapter(fail_actions={ActionType.CREATE_PAYMENT_LINK})
    )
    result = RazorpayVerifier(client).verify(_case(), _proposal(), execution)
    assert result.is_recovered is False
    assert result.verification_status is VerificationStatus.NOT_RECOVERED


def test_non_payment_action_has_nothing_to_confirm():
    client = _FakeClient()
    execution = _execution(ActionType.SEND_REMINDER, MockAdapter())
    result = RazorpayVerifier(client).verify(_case(), _proposal(), execution)
    assert result.verification_status is VerificationStatus.PENDING


def test_fetch_failure_is_treated_as_unconfirmed():
    client = _FakeClient(payment_link=RuntimeError("network down"))
    execution = _execution(ActionType.CREATE_PAYMENT_LINK, MockAdapter())
    result = RazorpayVerifier(client).verify(_case(), _proposal(), execution)
    assert result.verification_status is VerificationStatus.PENDING


def test_webhook_fed_status_source_can_confirm_recovery():
    # A webhook-fed status map can drive verification instead of polling.
    execution = _execution(ActionType.CREATE_PAYMENT_LINK, MockAdapter())
    verifier = RazorpayVerifier(
        client=None,
        status_source=lambda ex: {"status": "paid", "amount_paid": 150000, "id": "plink_1"},
    )
    result = verifier.verify(_case(), _proposal(), execution)
    assert result.is_recovered is True
    assert result.amount_recovered == Decimal("1500.00")


def _proposal() -> ActionProposal:
    return ActionProposal(
        case_id="case_x", action_type=ActionType.CREATE_PAYMENT_LINK,
        rationale="x", confidence=0.8,
    )
