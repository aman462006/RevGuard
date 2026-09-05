"""Real Razorpay Test Mode Payment Link intervention: creation, gating, idempotency, recovery.

These focus on the new behaviour: ``create_payment_link`` produces a *real* provider object
(captured ``short_url``), the PolicyEngine still gates it, the executor stays idempotent, and
recovery is declared ONLY by verified provider status — a created link is never recovery.
"""

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
from revguard.execution import ActionExecutor
from revguard.integrations.razorpay.adapter import RazorpayTestAdapter
from revguard.integrations.razorpay.verifier import RazorpayVerifier
from revguard.policy import PolicyEngine


# -- fakes -----------------------------------------------------------------------------
class _Resource:
    """A Razorpay resource fake supporting ``create`` and ``fetch``."""

    def __init__(self, *, create_ret=None, fetch_ret=None):
        self._create_ret = create_ret
        self._fetch_ret = fetch_ret
        self.create_calls: list = []
        self.fetch_calls: list = []

    def create(self, *args, **kwargs):
        self.create_calls.append((args, kwargs))
        if isinstance(self._create_ret, Exception):
            raise self._create_ret
        return self._create_ret

    def fetch(self, reference):
        self.fetch_calls.append(reference)
        return self._fetch_ret


class _Client:
    def __init__(self, *, payment_link=None, order=None):
        self.payment_link = payment_link or _Resource()
        self.order = order or _Resource()


def _case(amount: Decimal = Decimal("6000.00")) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.CHECKOUT_ABANDONMENT,
        risk_level=RiskLevel.MEDIUM,
        customer_id="cust_1",
        order_id="ord_1",
        amount_at_risk=amount,
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    return RecoveryCase(
        case_id="case_link",
        case_type=WorkflowType.CHECKOUT_ABANDONMENT,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=amount,
        currency=Currency.INR,
    )


def _proposal(amount: Decimal | None = None) -> ActionProposal:
    return ActionProposal(
        case_id="case_link",
        action_type=ActionType.CREATE_PAYMENT_LINK,
        rationale="send a payment link for the abandoned cart",
        confidence=0.9,
        expected_recovery_amount=amount,
        parameters={"idempotency_key": "case_link:create_payment_link:0"},
    )


def _approve() -> PolicyDecision:
    return PolicyDecision(
        case_id="case_link",
        decision=DecisionType.APPROVE,
        proposed_action=ActionType.CREATE_PAYMENT_LINK,
        reason="ok",
        matched_rules=["approve.permitted_action"],
    )


# -- creation captures the real link --------------------------------------------------
def test_create_payment_link_captures_short_url_and_reference():
    link = _Resource(
        create_ret={
            "id": "plink_TEST123",
            "status": "created",
            "short_url": "https://rzp.io/i/abc123",
        }
    )
    adapter = RazorpayTestAdapter(_Client(payment_link=link))
    result = adapter.create_payment_link(_case(), _proposal())

    payload = link.create_calls[0][0][0]
    assert payload["amount"] == 600000  # 6000.00 INR in paise
    assert payload["notes"]["case_id"] == "case_link"  # reconciliation key
    assert result.succeeded and result.simulated is False
    assert result.reference == "plink_TEST123"
    assert result.url == "https://rzp.io/i/abc123"  # customer-facing link surfaced


# -- policy gating --------------------------------------------------------------------
def test_policy_approves_payment_link_within_cap():
    decision = PolicyEngine().evaluate(_proposal(), _case(Decimal("6000.00")))
    assert decision.decision is DecisionType.APPROVE
    assert decision.proposed_action is ActionType.CREATE_PAYMENT_LINK


def test_policy_escalates_payment_link_over_amount_cap():
    # Above the escalation amount threshold (default ₹50,000) → no autonomous execution.
    big = _case(Decimal("90000.00"))
    proposal = ActionProposal(
        case_id="case_link",
        action_type=ActionType.CREATE_PAYMENT_LINK,
        rationale="large cart",
        confidence=0.9,
    )
    decision = PolicyEngine().evaluate(proposal, big)
    assert decision.decision is DecisionType.ESCALATE


# -- idempotency ----------------------------------------------------------------------
def test_executor_creates_payment_link_only_once_for_same_key():
    link = _Resource(create_ret={"id": "plink_1", "status": "created", "short_url": "u"})
    executor = ActionExecutor(RazorpayTestAdapter(_Client(payment_link=link)))
    case, proposal, decision = _case(), _proposal(), _approve()

    first = executor.execute(decision, proposal, case)
    second = executor.execute(decision, proposal, case)

    assert len(link.create_calls) == 1  # the real API is hit exactly once
    assert second.from_cache is True
    assert first.adapter_result.reference == second.adapter_result.reference


# -- verification-based recovery ------------------------------------------------------
def test_created_link_is_not_recovery_until_paid():
    # Link exists but is unpaid → PENDING, never recovered.
    link = _Resource(
        create_ret={"id": "plink_1", "status": "created", "short_url": "u"},
        fetch_ret={"id": "plink_1", "status": "created", "amount": 600000},
    )
    client = _Client(payment_link=link)
    adapter = RazorpayTestAdapter(client)
    case, proposal = _case(), _proposal()
    record = ActionExecutor(adapter).execute(_approve(), proposal, case)

    result = RazorpayVerifier(client).verify(case, proposal, record)
    assert result.verification_status is VerificationStatus.PENDING
    assert result.amount_recovered == Decimal("0")


def test_paid_link_is_verified_recovery():
    link = _Resource(
        create_ret={"id": "plink_1", "status": "created", "short_url": "u"},
        fetch_ret={"id": "plink_1", "status": "paid", "amount_paid": 600000},
    )
    client = _Client(payment_link=link)
    adapter = RazorpayTestAdapter(client)
    case, proposal = _case(), _proposal()
    record = ActionExecutor(adapter).execute(_approve(), proposal, case)

    result = RazorpayVerifier(client).verify(case, proposal, record)
    assert result.verification_status is VerificationStatus.RECOVERED
    assert result.amount_recovered == Decimal("6000.00")
    assert result.payment_reference == "plink_1"
