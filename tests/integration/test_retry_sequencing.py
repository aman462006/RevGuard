"""Deterministic retry sequencing through the real RecoveryAgent (Phase 7 extension).

A failed-but-recoverable case that re-proposes the same action too soon is not given up on:
the agent holds it WAITING with a persisted ``next_retry_at`` from the PolicyEngine's fixed
schedule, resumes it only once due, respects the attempt budget, and finally STOPs rather than
retrying forever. These drive the real agent against a temporary SQLite DB with an injectable
clock (no real time, no network, no AI). The `database` fixture is from conftest.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from revguard.diagnosis import Diagnoser, MockDiagnoser
from revguard.domain import (
    ActionProposal,
    ActionType,
    CaseStatus,
    Currency,
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


class _AlwaysRetry(Diagnoser):
    """A diagnoser that always proposes the same executable action (a real retry loop)."""

    name = "always_retry"

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        return ActionProposal(
            case_id=case.case_id,
            action_type=ActionType.RETRY_PAYMENT,
            rationale="retry the charge",
            confidence=0.8,
        )


class _Clock:
    """A controllable monotonic clock so retry timing is deterministic in tests."""

    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


def _case(**overrides) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=WorkflowType.FAILED_SUBSCRIPTION,
        risk_level=RiskLevel.HIGH,
        customer_id="cust_1",
        subscription_id="sub_1",
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    defaults = dict(
        case_type=WorkflowType.FAILED_SUBSCRIPTION,
        customer_id="cust_1",
        signal=signal,
        amount_at_risk=Decimal("1500.00"),
        currency=Currency.INR,
    )
    defaults.update(overrides)
    return RecoveryCase(**defaults)


def _agent(database: Database, clock: _Clock, **overrides) -> RecoveryAgent:
    kwargs = dict(
        diagnoser=_AlwaysRetry(),
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


# -- 1. the first failed attempt schedules a spaced retry rather than stopping ----------


def test_failed_attempt_schedules_next_retry_not_terminal(database: Database):
    clock = _Clock()
    adapter = MockAdapter()
    agent = _agent(database, clock, adapter=adapter)
    case = _case()

    agent.process_case(case)

    # Exactly one attempt executed; the case is held (non-terminal) for a scheduled retry.
    assert len(adapter.calls) == 1
    assert case.status is CaseStatus.WAITING
    assert case.is_terminal is False
    # Next retry is +30 minutes on the deterministic schedule (attempt 1 -> attempt 2 gap).
    assert case.next_retry_at == clock.now + timedelta(minutes=30)


# -- 2. cooldown enforcement: a retry before its scheduled time does not execute --------


def test_retry_not_executed_before_its_scheduled_time(database: Database):
    clock = _Clock()
    adapter = MockAdapter()
    agent = _agent(database, clock, adapter=adapter)
    case = _case()
    agent.process_case(case)
    assert len(adapter.calls) == 1
    scheduled = case.next_retry_at

    # Only 29 minutes pass — still inside the cooldown window. Re-running does nothing.
    clock.advance(minutes=29)
    agent.process_case(_load(database, case.case_id))

    after = _load(database, case.case_id)
    assert len(adapter.calls) == 1  # no new execution
    assert after.status is CaseStatus.WAITING
    assert after.next_retry_at == scheduled  # unchanged


# -- 3. schedule calculation across the whole cadence + attempt cap ---------------------


def test_full_retry_cadence_then_stops_at_attempt_budget(database: Database):
    clock = _Clock()
    adapter = MockAdapter()
    agent = _agent(database, clock, adapter=adapter)
    case = _case()

    # Attempt 1 (immediate).
    agent.process_case(case)
    assert len(adapter.calls) == 1
    assert _load(database, case.case_id).next_retry_at == clock.now + timedelta(minutes=30)

    # +30m -> attempt 2, which reschedules +6h.
    clock.advance(minutes=30)
    agent.process_case(_load(database, case.case_id))
    assert len(adapter.calls) == 2
    assert _load(database, case.case_id).next_retry_at == clock.now + timedelta(hours=6)

    # +6h -> attempt 3, which reschedules +24h.
    clock.advance(hours=6)
    agent.process_case(_load(database, case.case_id))
    assert len(adapter.calls) == 3
    assert _load(database, case.case_id).next_retry_at == clock.now + timedelta(hours=24)

    # +24h -> attempt 4 (the last slot); the budget is now exhausted -> STOP, no more retries.
    clock.advance(hours=24)
    final = agent.process_case(_load(database, case.case_id))

    assert len(adapter.calls) == 4  # exactly the attempt budget
    assert final.status is CaseStatus.STOPPED
    assert final.stop_reason is StopReason.MAX_ATTEMPTS_REACHED
    assert final.next_retry_at is None


# -- 4. persistence: the scheduled retry survives a "restart" (fresh agent + DB reload) --


def test_scheduled_retry_persists_across_restart(database: Database):
    clock = _Clock()
    case = _case()
    _agent(database, clock).process_case(case)

    reloaded = _load(database, case.case_id)
    assert reloaded.next_retry_at == clock.now + timedelta(minutes=30)  # persisted, not in-memory

    # A brand-new agent (simulating a process restart) resumes the retry once it is due.
    clock2 = _Clock()
    clock2.now = clock.now + timedelta(minutes=30)
    adapter = MockAdapter()
    agent2 = _agent(database, clock2, adapter=adapter)
    agent2.process_case(reloaded)

    assert len(adapter.calls) == 1  # the resumed attempt executed after restart
    assert _load(database, case.case_id).attempt_count == 2


# -- 5. terminal stopping: an exhausted case is never retried again ----------------------


def test_stopped_case_is_never_retried_again(database: Database):
    clock = _Clock()
    adapter = MockAdapter()
    agent = _agent(database, clock, adapter=adapter)
    case = _case()
    agent.process_case(case)  # attempt 1 (persists the case)
    for delta in (dict(minutes=30), dict(hours=6), dict(hours=24)):
        clock.advance(**delta)
        agent.process_case(_load(database, case.case_id))
    assert _load(database, case.case_id).status is CaseStatus.STOPPED
    calls_at_stop = len(adapter.calls)

    # Far past every scheduled time — a terminal case must not execute anything more.
    clock.advance(days=3)
    agent.process_case(_load(database, case.case_id))
    assert len(adapter.calls) == calls_at_stop


# -- 6. successful recovery before the next retry closes the case (no further attempts) --


def test_recovery_before_next_retry_stops_retrying(database: Database):
    clock = _Clock()
    adapter = MockAdapter()
    agent = _agent(database, clock, adapter=adapter)
    case = _case()
    agent.process_case(case)  # attempt 1, now WAITING with a scheduled retry
    assert _load(database, case.case_id).status is CaseStatus.WAITING
    assert len(adapter.calls) == 1

    # The customer pays before the +30m retry — reconciled through the verified path.
    recon = agent.simulate_test_recovery(_load(database, case.case_id))
    assert recon.reconciled is True

    recovered = _load(database, case.case_id)
    assert recovered.status is CaseStatus.RECOVERED
    assert recovered.amount_recovered == Decimal("1500.00")

    # Even well past the scheduled retry time, a recovered case never retries again.
    clock.advance(hours=1)
    agent.process_case(recovered)
    assert len(adapter.calls) == 1  # no further attempts after recovery


# -- 7. the standard (varied-action) flow is unchanged: no premature scheduling ----------


def test_distinct_actions_are_not_delayed_by_the_retry_schedule(database: Database):
    # MockDiagnoser proposes different actions per step, so the first non-recovery moves on to
    # a second action immediately (the retry schedule only spaces *repeats* of the same action).
    clock = _Clock()
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        verifier=MockVerifier(
            sequence=[VerificationStatus.NOT_RECOVERED, VerificationStatus.RECOVERED]
        ),
        adapter=adapter,
        clock=clock,
    )
    case = _case()
    agent.process_case(case)

    assert [a for a, _ in adapter.calls] == [
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
    ]
    assert case.status is CaseStatus.RECOVERED
