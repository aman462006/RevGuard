"""RazorpayTestAdapter: real Test Mode calls for payments, delegation, and no leakage."""

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
from revguard.integrations.razorpay.adapter import RazorpayTestAdapter


class _FakeResource:
    def __init__(self, ret, calls):
        self._ret = ret
        self._calls = calls

    def create(self, *args, **kwargs):
        self._calls.append((args, kwargs))
        if isinstance(self._ret, Exception):
            raise self._ret
        return self._ret


class _FakeClient:
    def __init__(self, *, order=None, payment_link=None):
        self.order_calls: list = []
        self.link_calls: list = []
        self.order = _FakeResource(order, self.order_calls)
        self.payment_link = _FakeResource(payment_link, self.link_calls)


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


def _proposal(action: ActionType) -> ActionProposal:
    return ActionProposal(
        case_id="case_x", action_type=action, rationale="x", confidence=0.8,
        parameters={"idempotency_key": "case_x:key:0"},
    )


def test_create_payment_link_calls_razorpay_and_reports_technical_success():
    client = _FakeClient(payment_link={"id": "plink_1", "status": "created",
                                        "short_url": "https://rzp.io/i/abc"})
    adapter = RazorpayTestAdapter(client)
    result = adapter.create_payment_link(_case(), _proposal(ActionType.CREATE_PAYMENT_LINK))

    (args, kwargs) = client.link_calls[0]
    payload = args[0] if args else kwargs.get("data")
    assert payload["amount"] == 150000  # 1500.00 INR in paise
    assert payload["reference_id"] == "case_x:key:0"  # idempotency preserved
    assert result.accepted and result.succeeded
    assert result.simulated is False
    assert result.reference == "plink_1"


def test_retry_payment_creates_order_in_paise():
    client = _FakeClient(order={"id": "order_1", "status": "created"})
    adapter = RazorpayTestAdapter(client)
    result = adapter.retry_payment(_case(), _proposal(ActionType.RETRY_PAYMENT))

    (_args, kwargs) = client.order_calls[0]
    assert kwargs["data"]["amount"] == 150000
    assert kwargs["data"]["receipt"] == "case_x:key:0"
    assert result.reference == "order_1"
    assert result.simulated is False


def test_api_error_is_a_technical_failure_without_leaking_details():
    secret_message = "auth failed for rzp_test_SECRETKEY"
    client = _FakeClient(payment_link=RuntimeError(secret_message))
    adapter = RazorpayTestAdapter(client)
    result = adapter.create_payment_link(_case(), _proposal(ActionType.CREATE_PAYMENT_LINK))

    assert result.accepted is False and result.succeeded is False
    assert result.failure_reason == "RuntimeError"  # type only
    # The raw exception message (which could carry credential-shaped text) never surfaces.
    assert secret_message not in result.detail
    assert result.failure_reason is not None and secret_message not in result.failure_reason


def test_non_payment_actions_delegate_to_fallback():
    fallback = MockAdapter()
    adapter = RazorpayTestAdapter(_FakeClient(), fallback=fallback)
    r1 = adapter.send_reminder(_case(), _proposal(ActionType.SEND_REMINDER))
    r2 = adapter.record_promise_to_pay(_case(), _proposal(ActionType.RECORD_PROMISE_TO_PAY))
    r3 = adapter.wait(_case(), _proposal(ActionType.WAIT))
    # Handled by the fallback (simulated), not by Razorpay.
    assert r1.simulated and r2.simulated and r3.simulated
    assert {a for a, _ in fallback.calls} == {
        ActionType.SEND_REMINDER, ActionType.RECORD_PROMISE_TO_PAY, ActionType.WAIT
    }


def test_executor_run_does_not_claim_recovery_on_api_success():
    """A successful Razorpay create is technically SUCCEEDED but recovery stays PENDING."""
    client = _FakeClient(payment_link={"id": "plink_1", "status": "created"})
    adapter = RazorpayTestAdapter(client)
    proposal = _proposal(ActionType.CREATE_PAYMENT_LINK)
    decision = PolicyDecision(
        case_id="case_x",
        decision=DecisionType.APPROVE,
        proposed_action=ActionType.CREATE_PAYMENT_LINK,
        reason="ok",
        matched_rules=["approve.permitted_action"],
    )
    record = ActionExecutor(adapter).execute(decision, proposal, _case())
    assert record.result.verification_status is VerificationStatus.PENDING
    assert record.result.amount_recovered == Decimal("0")
    assert record.adapter_result.simulated is False
