"""Promise-to-pay as a bounded, stateful workflow (Workflow D extension).

A recorded promise is a commitment, not recovery. These drive the real RecoveryAgent against a
temporary SQLite DB with an injectable clock to exercise the full lifecycle:

    PROMISED → PENDING → KEPT/RECOVERED (verified payment)  or  MISSED → ESCALATE/STOP

plus persistence, duplicate prevention, and terminal-case safety. No network, no real AI.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from revguard.diagnosis import Diagnoser
from revguard.domain import (
    ActionProposal,
    ActionType,
    CaseStatus,
    Currency,
    PromiseStatus,
    PromiseToPay,
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
from revguard.verification import MockVerifier


class _PromiseDiagnoser(Diagnoser):
    """Always proposes a promise-to-pay, optionally with an explicit promised date."""

    name = "promise"

    def __init__(self, promised_at: datetime | None = None) -> None:
        self._promised_at = promised_at

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        params = {}
        if self._promised_at is not None:
            params["promised_date"] = self._promised_at.isoformat()
        return ActionProposal(
            case_id=case.case_id,
            action_type=ActionType.RECORD_PROMISE_TO_PAY,
            rationale="customer committed to pay",
            confidence=0.9,
            parameters=params,
        )


class _Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def _overdue_case(**overrides) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.OVERDUE_RECEIVABLE,
        risk_level=RiskLevel.MEDIUM,
        customer_id="cust_1",
        invoice_id="inv_1",
        amount_at_risk=Decimal("5000.00"),
        currency=Currency.INR,
        evidence={"days_overdue": 40},
        source_event_ids=["evt_1"],
    )
    defaults = dict(
        case_type=WorkflowType.OVERDUE_RECEIVABLE,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=Decimal("5000.00"),
        currency=Currency.INR,
    )
    defaults.update(overrides)
    return RecoveryCase(**defaults)


def _agent(database: Database, clock: _Clock, **overrides) -> RecoveryAgent:
    kwargs = dict(
        diagnoser=_PromiseDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.NOT_RECOVERED),
        adapter=MockAdapter(),
        clock=clock,
    )
    kwargs.update(overrides)
    return RecoveryAgent(database, **kwargs)


def _load(database: Database, case_id: str) -> RecoveryCase:
    with database.session() as s:
        case = CaseRepository(s).get(case_id)
    assert case is not None
    return case


def _promise_case(
    clock: _Clock,
    *,
    promise_status: PromiseStatus = PromiseStatus.PROMISED,
    promised_at: datetime | None = None,
    case_status: CaseStatus = CaseStatus.WAITING,
    **overrides,
) -> RecoveryCase:
    """A case that already carries a promise (for resolution/terminal tests)."""
    promised_at = promised_at or (clock.now - timedelta(minutes=1))  # due by default
    promise = PromiseToPay(
        case_id="seed",
        promised_at=promised_at,
        recorded_at=clock.now - timedelta(days=1),
        status=promise_status,
        amount=Decimal("5000.00"),
        currency=Currency.INR,
        reference="sim_ptp_seed",
    )
    case = _overdue_case(status=case_status, promise=promise, attempt_count=1, **overrides)
    # Align the embedded promise's case_id with the generated case id.
    case.promise = promise.model_copy(update={"case_id": case.case_id})
    return case


# -- 1. recording a promise is not recovery: it enters PROMISED and waits ----------------


def test_recording_a_promise_is_not_recovery(database: Database):
    clock = _Clock()
    promised = clock.now + timedelta(days=3)
    adapter = MockAdapter()
    agent = _agent(database, clock, diagnoser=_PromiseDiagnoser(promised), adapter=adapter)
    case = _overdue_case()

    agent.process_case(case)

    assert [a for a, _ in adapter.calls] == [ActionType.RECORD_PROMISE_TO_PAY]
    assert case.status is CaseStatus.WAITING
    assert case.amount_recovered == Decimal("0")  # never recovered merely for recording
    assert case.promise is not None
    assert case.promise.status is PromiseStatus.PROMISED
    assert case.promise.promised_at == promised
    assert case.promise.case_id == case.case_id  # case reference persisted on the promise


# -- 2. before the promised time nothing is verified (stays PROMISED) --------------------


def test_promise_not_verified_before_its_time(database: Database):
    clock = _Clock()
    promised = clock.now + timedelta(days=3)
    agent = _agent(
        database, clock, diagnoser=_PromiseDiagnoser(promised),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
    )
    case = _overdue_case()
    agent.process_case(case)

    # Re-running well before the promised time must not verify or recover.
    clock.advance(days=1)
    agent.process_case(_load(database, case.case_id))
    after = _load(database, case.case_id)
    assert after.status is CaseStatus.WAITING
    assert after.promise.status is PromiseStatus.PROMISED
    assert after.amount_recovered == Decimal("0")


# -- 3. successful verification on/after the promised time -> KEPT + RECOVERED -----------


def test_promise_kept_verifies_and_recovers(database: Database):
    clock = _Clock()
    promised = clock.now + timedelta(days=2)
    agent = _agent(
        database, clock, diagnoser=_PromiseDiagnoser(promised),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
    )
    case = _overdue_case()
    agent.process_case(case)
    assert _load(database, case.case_id).promise.status is PromiseStatus.PROMISED

    clock.advance(days=2, minutes=1)  # promised time reached
    agent.check_due_promise(_load(database, case.case_id))

    recovered = _load(database, case.case_id)
    assert recovered.status is CaseStatus.RECOVERED
    assert recovered.amount_recovered == Decimal("5000.00")
    assert recovered.promise.status is PromiseStatus.KEPT
    assert recovered.promise.resolved_at is not None


# -- 4. a missed promise escalates through the PolicyEngine ------------------------------


def test_missed_promise_escalates(database: Database):
    clock = _Clock()
    agent = _agent(database, clock, verifier=MockVerifier(default=VerificationStatus.NOT_RECOVERED))
    case = _promise_case(clock)  # already WAITING, promise due, no payment
    with database.session() as s:
        CaseRepository(s).add(case)

    agent.check_due_promise(case)

    missed = _load(database, case.case_id)
    assert missed.promise.status is PromiseStatus.MISSED
    assert missed.status is CaseStatus.ESCALATED
    assert missed.escalated_at is not None
    assert "promise" in (missed.escalation_reason or "").lower()


# -- 5. a missed promise on an expired case takes the PolicyEngine STOP path -------------


def test_missed_promise_on_expired_case_stops(database: Database):
    clock = _Clock()
    agent = _agent(database, clock, verifier=MockVerifier(default=VerificationStatus.NOT_RECOVERED))
    case = _promise_case(clock, expires_at=clock.now - timedelta(hours=1))
    with database.session() as s:
        CaseRepository(s).add(case)

    agent.check_due_promise(case)

    out = _load(database, case.case_id)
    assert out.promise.status is PromiseStatus.MISSED
    assert out.status is CaseStatus.STOPPED
    assert out.stop_reason is StopReason.CASE_EXPIRED


# -- 6. duplicate promises are never auto-approved (deterministic via PolicyEngine) ------


def test_duplicate_promise_is_not_recorded(database: Database):
    clock = _Clock()
    adapter = MockAdapter()
    # Diagnoser keeps proposing promises; an open promise already exists.
    agent = _agent(database, clock, adapter=adapter)
    case = _promise_case(clock, promised_at=clock.now + timedelta(days=2))  # open, not due
    with database.session() as s:
        CaseRepository(s).add(case)

    agent.process_case(case)

    after = _load(database, case.case_id)
    # No second promise recorded and no adapter call — the PolicyEngine blocks the duplicate.
    assert adapter.calls == []
    assert after.promise.reference == "sim_ptp_seed"  # still the original promise


# -- 7. terminal cases: check_due_promise never acts on them ----------------------------


def test_check_due_promise_ignores_terminal_case(database: Database):
    clock = _Clock()
    agent = _agent(database, clock, verifier=MockVerifier(default=VerificationStatus.RECOVERED))
    # A terminal (stopped) case whose promise is still open and past due.
    case = _promise_case(
        clock,
        case_status=CaseStatus.STOPPED,
        stopped_at=clock.now,
        stop_reason=StopReason.MANUAL_STOP,
    )
    with database.session() as s:
        CaseRepository(s).add(case)

    agent.check_due_promise(case)

    out = _load(database, case.case_id)
    assert out.status is CaseStatus.STOPPED  # unchanged
    assert out.promise.status is PromiseStatus.PROMISED  # not verified/advanced
    assert out.amount_recovered == Decimal("0")


# -- 8. persistence: the promise survives a "restart" and resolves afterwards ------------


def test_promise_persists_and_resolves_after_restart(database: Database):
    clock = _Clock()
    promised = clock.now + timedelta(days=1)
    _agent(
        database, clock, diagnoser=_PromiseDiagnoser(promised),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
    ).process_case(_overdue_case())

    with database.session() as s:
        cases = CaseRepository(s).list_all()
    assert len(cases) == 1
    reloaded = cases[0]
    assert reloaded.promise is not None
    assert reloaded.promise.status is PromiseStatus.PROMISED
    assert reloaded.promise.promised_at == promised  # persisted date, not in-memory

    # A brand-new agent (simulated restart), after the promised time, resolves it to recovered.
    clock2 = _Clock()
    clock2.now = promised + timedelta(minutes=1)
    RecoveryAgent(
        database, diagnoser=_PromiseDiagnoser(),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
        adapter=MockAdapter(), clock=clock2,
    ).check_due_promise(reloaded)

    final = _load(database, reloaded.case_id)
    assert final.status is CaseStatus.RECOVERED
    assert final.promise.status is PromiseStatus.KEPT


# -- 9. a promise already paid out-of-band (case RECOVERED) is marked KEPT ---------------


def test_promise_on_recovered_case_is_marked_kept(database: Database):
    clock = _Clock()
    agent = _agent(database, clock)
    # Case already recovered (e.g. a verified webhook) while the promise was still open.
    case = _overdue_case(
        status=CaseStatus.RECOVERED, amount_recovered=Decimal("5000.00")
    )
    promise = PromiseToPay(
        case_id=case.case_id,
        promised_at=clock.now + timedelta(days=1),
        status=PromiseStatus.PROMISED,
        amount=Decimal("5000.00"),
        currency=Currency.INR,
    )
    case.promise = promise
    with database.session() as s:
        CaseRepository(s).add(case)

    agent.check_due_promise(case)

    out = _load(database, case.case_id)
    assert out.status is CaseStatus.RECOVERED
    assert out.promise.status is PromiseStatus.KEPT
