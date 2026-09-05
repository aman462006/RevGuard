"""End-to-end orchestrator flows (Phase 7).

Each test drives the real RecoveryAgent against a temporary SQLite database, using the
deterministic MockDiagnoser / MockVerifier / MockAdapter (no network, no real AI). The
`database` fixture comes from tests/integration/conftest.py.
"""

from __future__ import annotations

from decimal import Decimal

from revguard.audit import AuditLog, AuditStage
from revguard.diagnosis import Diagnoser, MockDiagnoser
from revguard.domain import (
    ActionProposal,
    ActionType,
    CaseStatus,
    Currency,
    Event,
    EventSource,
    EventType,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    StopReason,
    VerificationStatus,
    WorkflowType,
    utcnow,
)
from revguard.execution import MockAdapter
from revguard.orchestrator import RecoveryAgent
from revguard.persistence import CaseRepository, Database
from revguard.policy import PolicyConfig
from revguard.verification import MockVerifier

# -- factories -------------------------------------------------------------------------


def _signal(workflow: WorkflowType, *, amount: Decimal, risk: RiskLevel) -> RevenueRiskSignal:
    return RevenueRiskSignal(
        signal_type=workflow,
        risk_level=risk,
        customer_id="cust_1",
        subscription_id="sub_1" if workflow is WorkflowType.FAILED_SUBSCRIPTION else None,
        order_id="ord_1" if workflow is WorkflowType.CHECKOUT_ABANDONMENT else None,
        invoice_id="inv_1" if workflow is WorkflowType.OVERDUE_RECEIVABLE else None,
        amount_at_risk=amount,
        currency=Currency.INR,
        evidence={"failure_count": 2, "days_overdue": 40},
        source_event_ids=["evt_1"],
    )


def _case(
    workflow: WorkflowType = WorkflowType.FAILED_SUBSCRIPTION,
    *,
    amount: Decimal = Decimal("1500.00"),
    risk: RiskLevel = RiskLevel.HIGH,
    **overrides,
) -> RecoveryCase:
    signal = _signal(workflow, amount=amount, risk=risk)
    defaults = dict(
        case_type=workflow,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=amount,
        currency=Currency.INR,
    )
    defaults.update(overrides)
    return RecoveryCase(**defaults)


def _sub_failed_events(n: int) -> list[Event]:
    return [
        Event(
            event_type=EventType.SUBSCRIPTION_PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            customer_id="cust_1",
            subscription_id="sub_1",
            amount=Decimal("1500.00"),
            currency=Currency.INR,
            metadata={"failure_reason": "card_declined"},
        )
        for _ in range(n)
    ]


class _AlwaysRetry(Diagnoser):
    name = "always_retry"

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        return ActionProposal(
            case_id=case.case_id,
            action_type=ActionType.RETRY_PAYMENT,
            rationale="always retry",
            confidence=0.8,
        )


class _BrokenDiagnoser(Diagnoser):
    name = "broken"

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        raise RuntimeError("provider exploded")


class _WrongCaseDiagnoser(Diagnoser):
    name = "wrong_case"

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        return ActionProposal(
            case_id="case_ATTACKER",
            action_type=ActionType.RETRY_PAYMENT,
            rationale="malformed",
            confidence=0.9,
        )


def _stages(database: Database, case_id: str) -> list[AuditStage]:
    with database.session() as s:
        return [e.stage for e in AuditLog(s).for_case(case_id)]


# -- A. successful recovery (+ I. audit trail) -----------------------------------------


def test_a_successful_recovery(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
        adapter=adapter,
    )
    case = _case()
    agent.process_case(case)

    assert case.status is CaseStatus.RECOVERED
    assert case.amount_recovered == Decimal("1500.00")
    assert [a for a, _ in adapter.calls] == [ActionType.RETRY_PAYMENT]

    # I. audit trail contains the expected sequence of stages.
    assert _stages(database, case.case_id) == [
        AuditStage.CASE_CREATED,
        AuditStage.DIAGNOSIS,
        AuditStage.POLICY_DECISION,
        AuditStage.EXECUTION,
        AuditStage.VERIFICATION,
        AuditStage.RECOVERY_RESULT,
    ]


# -- B. failed first attempt then a second action --------------------------------------


def test_b_failed_first_attempt_then_second_action(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(
            sequence=[VerificationStatus.NOT_RECOVERED, VerificationStatus.RECOVERED]
        ),
        adapter=adapter,
    )
    case = _case()
    agent.process_case(case)

    # First attempt did not recover → policy allowed the next step (a different action).
    assert [a for a, _ in adapter.calls] == [
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
    ]
    assert case.status is CaseStatus.RECOVERED


# -- C. maximum attempts → STOP → no further execution ---------------------------------


def test_c_maximum_attempts_stops(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=_AlwaysRetry(),
        verifier=MockVerifier(default=VerificationStatus.NOT_RECOVERED),
        adapter=adapter,
        config=PolicyConfig(cooldown_seconds=0),  # isolate the attempt cap from cooldown
    )
    case = _case()
    agent.process_case(case)

    assert len(adapter.calls) == 4  # PolicyConfig.max_attempts
    assert case.status is CaseStatus.STOPPED
    assert case.stop_reason is StopReason.MAX_ATTEMPTS_REACHED


# -- D. escalation → no executor call --------------------------------------------------


def test_d_escalation_does_not_execute(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(),
        adapter=adapter,
    )
    case = _case(amount=Decimal("90000.00"))  # over the escalation threshold
    agent.process_case(case)

    assert case.status is CaseStatus.ESCALATED
    assert case.escalated_at is not None
    assert case.escalation_reason
    assert adapter.calls == []


def test_d_human_authorizes_escalated_case_and_recovers(database: Database):
    # An escalated case is in the human queue; a human operator authorizes a bounded action,
    # which really executes and (with a verifier that confirms) recovers — actor=HUMAN in audit.
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(
            default=VerificationStatus.RECOVERED, recovered_amount=Decimal("90000.00")
        ),
        adapter=adapter,
    )
    case = _case(amount=Decimal("90000.00"))
    agent.process_case(case)
    assert case.status is CaseStatus.ESCALATED
    assert adapter.calls == []  # nothing executed autonomously

    agent.authorize(case, ActionType.RETRY_PAYMENT, operator="ops_alice")

    assert case.status is CaseStatus.RECOVERED
    assert case.amount_recovered == Decimal("90000.00")
    assert len(adapter.calls) == 1  # the human-authorized action executed exactly once
    # The authorization is recorded as a HUMAN policy decision.
    with database.session() as s:
        entries = AuditLog(s).for_case(case.case_id)
    human = [e for e in entries if e.actor.value == "human"]
    assert human and human[-1].details.get("human_override") is True


def test_d_human_authorize_rejects_non_escalated_case(database: Database):
    from revguard.orchestrator import HumanReviewError

    agent = RecoveryAgent(
        database, diagnoser=MockDiagnoser(), verifier=MockVerifier(), adapter=MockAdapter()
    )
    case = _case(amount=Decimal("1500.00"))  # freshly detected, not escalated
    try:
        agent.authorize(case, ActionType.RETRY_PAYMENT)
    except HumanReviewError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected HumanReviewError for a non-escalated case")


def test_d_human_authorize_blocks_contact_action_without_consent(database: Database):
    # Consent/DND gate: a customer-contact action must not run when do_not_contact is set,
    # even on the human path — nothing is executed and the case stays escalated.
    from revguard.orchestrator import HumanReviewError

    adapter = MockAdapter()
    agent = RecoveryAgent(
        database, diagnoser=MockDiagnoser(), verifier=MockVerifier(), adapter=adapter
    )
    case = _case(
        workflow=WorkflowType.CHECKOUT_ABANDONMENT,
        amount=Decimal("6000.00"),
        status=CaseStatus.ESCALATED,
        escalated_at=utcnow(),
        escalation_reason="human review",
        do_not_contact=True,
    )
    for action in (ActionType.SEND_REMINDER, ActionType.CREATE_PAYMENT_LINK):
        try:
            agent.authorize(case, action)
        except HumanReviewError as exc:
            assert "do_not_contact" in str(exc)
        else:  # pragma: no cover
            raise AssertionError(f"expected consent block for {action}")
    assert adapter.calls == []  # no communication action ran
    assert case.status is CaseStatus.ESCALATED  # unchanged


def test_d_human_authorize_allows_non_contact_action_despite_do_not_contact(database: Database):
    # do_not_contact only gates *contact* actions; a non-contact action (retry_payment) still
    # runs when a human authorizes it.
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(
            default=VerificationStatus.RECOVERED, recovered_amount=Decimal("1500.00")
        ),
        adapter=adapter,
    )
    case = _case(
        amount=Decimal("1500.00"),
        status=CaseStatus.ESCALATED,
        escalated_at=utcnow(),
        escalation_reason="human review",
        do_not_contact=True,
    )
    agent.authorize(case, ActionType.RETRY_PAYMENT)
    assert len(adapter.calls) == 1  # non-contact action is not gated by consent


# -- E. already recovered cannot execute -----------------------------------------------


def test_e_already_recovered_cannot_execute(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(),
        adapter=adapter,
    )
    # A non-terminal case whose amount is already fully recovered.
    case = _case(
        amount=Decimal("1500.00"),
        amount_recovered=Decimal("1500.00"),
        status=CaseStatus.ACTION_PENDING,
    )
    agent.process_case(case)

    assert case.status is CaseStatus.STOPPED
    assert case.stop_reason is StopReason.ALREADY_RECOVERED
    assert adapter.calls == []


def test_e_terminal_case_is_not_processed_again(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(database, diagnoser=MockDiagnoser(), adapter=adapter)
    case = _case(amount_recovered=Decimal("1500.00"), status=CaseStatus.RECOVERED)
    agent.process_case(case)
    assert adapter.calls == []


# -- F. duplicate processing → no duplicate side effect --------------------------------


def test_f_duplicate_events_do_not_duplicate_work(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
        adapter=adapter,
    )
    events = _sub_failed_events(2)

    first = agent.process_events(events)
    calls_after_first = len(adapter.calls)
    assert len(first) == 1

    # Re-processing the identical events creates no new case and no new execution.
    second = agent.process_events(events)
    assert second == []
    assert len(adapter.calls) == calls_after_first


# -- G. pending verification → case remains non-terminal -------------------------------


def test_g_pending_verification_keeps_case_open(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.PENDING),
        adapter=adapter,
    )
    case = _case()
    agent.process_case(case)

    assert case.status is CaseStatus.WAITING
    assert case.is_terminal is False
    assert case.amount_recovered == 0
    assert len(adapter.calls) == 1


def test_g_simulate_test_recovery_confirms_waiting_case(database: Database):
    # A WAITING case (action executed, verification pending) is driven to RECOVERED through the
    # verified reconciliation path — the Test Mode "customer paid" simulation.
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.PENDING),
        adapter=MockAdapter(),
    )
    case = _case()
    agent.process_case(case)
    assert case.status is CaseStatus.WAITING

    recon = agent.simulate_test_recovery(case)

    assert recon.reconciled is True
    with database.session() as s:
        refreshed = CaseRepository(s).get(case.case_id)
    assert refreshed is not None
    assert refreshed.status is CaseStatus.RECOVERED
    assert refreshed.amount_recovered == case.amount_at_risk


# -- H. all four workflow types run through the same architecture ----------------------


def test_h_all_four_workflows_execute(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.PENDING),
        adapter=adapter,
    )
    cases = [
        _case(WorkflowType.PAYMENT_DEGRADATION, risk=RiskLevel.LOW),
        _case(WorkflowType.FAILED_SUBSCRIPTION),
        _case(WorkflowType.CHECKOUT_ABANDONMENT),
        _case(WorkflowType.OVERDUE_RECEIVABLE),
    ]
    for case in cases:
        agent.process_case(case)

    assert {a for a, _ in adapter.calls} == {
        ActionType.WAIT,
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
    }
    assert all(c.status is CaseStatus.WAITING for c in cases)


# -- J. recovered amount comes ONLY from verification ----------------------------------


def test_j_recovered_amount_comes_from_verifier(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        # Verifier reports a partial amount that differs from amount_at_risk (1500) and
        # from the executor's 0 — proving the number originates in verification.
        verifier=MockVerifier(
            default=VerificationStatus.RECOVERED, recovered_amount=Decimal("750.00")
        ),
        adapter=adapter,
    )
    case = _case(amount=Decimal("1500.00"))
    agent.process_case(case)

    assert case.status is CaseStatus.RECOVERED
    assert case.amount_recovered == Decimal("750.00")


# -- K. AI failure / malformed proposal cannot cause execution -------------------------


def test_k_broken_diagnoser_cannot_execute(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(database, diagnoser=_BrokenDiagnoser(), adapter=adapter)
    case = _case()
    agent.process_case(case)

    assert case.status is CaseStatus.ESCALATED
    assert adapter.calls == []


def test_k_malformed_proposal_cannot_execute(database: Database):
    adapter = MockAdapter()
    agent = RecoveryAgent(database, diagnoser=_WrongCaseDiagnoser(), adapter=adapter)
    case = _case()
    agent.process_case(case)

    assert case.status is CaseStatus.ESCALATED
    assert adapter.calls == []
