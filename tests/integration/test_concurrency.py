"""Concurrency safety for case execution (atomic claim).

Two simultaneous ``process_case`` runs for the same case must never both execute — exactly one
external intervention may be created. These tests use a **file-based** SQLite database (so two
threads share committed state) and count the adapter's payment actions to prove exclusivity.
They also prove the lock is always released (success or failure) and that an abandoned lock is
stolen after its TTL, so a case can never become permanently stuck.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from revguard.diagnosis import MockDiagnoser
from revguard.domain import (
    ActionType,
    CaseStatus,
    Currency,
    Event,
    EventSource,
    EventType,
    VerificationStatus,
)
from revguard.execution import MockAdapter
from revguard.orchestrator import RecoveryAgent
from revguard.persistence import CaseRepository, Database
from revguard.verification import MockVerifier

# Only the payment-moving actions are "external interventions" we must never duplicate.
_PAYMENT_ACTIONS = {ActionType.RETRY_PAYMENT, ActionType.CREATE_PAYMENT_LINK}
_TERMINAL_VALUES = ("recovered", "escalated", "stopped", "failed")


def _sub_event() -> Event:
    return Event(
        event_type=EventType.SUBSCRIPTION_PAYMENT_FAILED,
        source=EventSource.INTERNAL,
        customer_id="cust_conc",
        subscription_id="sub_conc",
        amount=Decimal("1200.00"),
        currency=Currency.INR,
        metadata={"failure_reason": "card_declined"},
    )


class _SlowAdapter(MockAdapter):
    """MockAdapter that pauses inside a payment action to widen the concurrency window."""

    def __init__(self, *, delay: float = 0.15) -> None:
        super().__init__()
        self._delay = delay

    def retry_payment(self, case, proposal):  # noqa: ANN001
        time.sleep(self._delay)
        return super().retry_payment(case, proposal)

    def create_payment_link(self, case, proposal):  # noqa: ANN001
        time.sleep(self._delay)
        return super().create_payment_link(case, proposal)


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'conc.db'}")
    database.create_all()
    yield database
    database.dispose()


def _agent(database: Database, adapter: MockAdapter) -> RecoveryAgent:
    return RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        adapter=adapter,
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
    )


def _create_detected_case(database: Database) -> str:
    """Ingest a failed-subscription event to create a DETECTED case (no run)."""
    cases = _agent(database, MockAdapter()).ingest_event(_sub_event())
    assert cases, "expected the failed-subscription event to create a case"
    return cases[0].case_id


def _payment_calls(adapter: MockAdapter) -> list:
    return [c for c in adapter.calls if c[0] in _PAYMENT_ACTIONS]


def _get(database: Database, case_id: str):
    with database.session() as s:
        return CaseRepository(s).get(case_id)


def _is_locked(database: Database, case_id: str) -> bool:
    with database.session() as s:
        return CaseRepository(s).is_locked(case_id)


# ---------------------------------------------------------------------------
# Concurrency: two simultaneous runs execute exactly one intervention
# ---------------------------------------------------------------------------


def test_two_concurrent_runs_execute_exactly_one_intervention(db):
    case_id = _create_detected_case(db)
    shared_adapter = _SlowAdapter(delay=0.15)  # shared "provider" across both runs
    barrier = threading.Barrier(2)
    outcomes: dict[str, CaseStatus] = {}

    def worker(name: str) -> None:
        # A fresh agent + case per "request", mirroring the API (separate agents, shared DB).
        agent = _agent(db, shared_adapter)
        case = _get(db, case_id)
        barrier.wait()  # release both threads together to maximise contention
        result = agent.process_case(case)
        outcomes[name] = result.status

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # The core guarantee: exactly ONE external intervention was created.
    assert len(_payment_calls(shared_adapter)) == 1

    final = _get(db, case_id)
    assert final.status is CaseStatus.RECOVERED
    assert final.amount_recovered == Decimal("1200.00")  # verified, capped at amount at risk
    assert final.attempt_count == 1  # not double-attempted
    assert _is_locked(db, case_id) is False  # lock released


def test_sequential_reruns_do_not_reexecute_a_recovered_case(db):
    """Belt-and-braces: after recovery, re-running is a no-op (terminal, and never re-claimed)."""
    case_id = _create_detected_case(db)
    adapter = MockAdapter()
    agent = _agent(db, adapter)

    agent.process_case(_get(db, case_id))
    agent.process_case(_get(db, case_id))  # second run on the now-terminal case

    assert len(_payment_calls(adapter)) == 1
    assert _get(db, case_id).status is CaseStatus.RECOVERED
    assert _is_locked(db, case_id) is False


# ---------------------------------------------------------------------------
# Successful execution
# ---------------------------------------------------------------------------


def test_successful_execution_recovers_and_releases_lock(db):
    case_id = _create_detected_case(db)
    adapter = MockAdapter()
    result = _agent(db, adapter).process_case(_get(db, case_id))

    assert result.status is CaseStatus.RECOVERED
    assert result.amount_recovered == Decimal("1200.00")
    assert len(_payment_calls(adapter)) == 1
    assert _is_locked(db, case_id) is False


# ---------------------------------------------------------------------------
# Failed execution
# ---------------------------------------------------------------------------


def test_failed_execution_recovers_nothing_and_releases_lock(db):
    case_id = _create_detected_case(db)
    # Every recovery action fails technically → the case can never be verified as recovered.
    failing = MockAdapter(
        fail_actions=frozenset(
            {
                ActionType.RETRY_PAYMENT,
                ActionType.CREATE_PAYMENT_LINK,
                ActionType.SEND_REMINDER,
            }
        )
    )
    result = _agent(db, failing).process_case(_get(db, case_id))

    assert result.is_terminal  # bounded: escalated or stopped, never infinite retry
    assert result.status in {CaseStatus.ESCALATED, CaseStatus.STOPPED}
    assert result.amount_recovered == Decimal("0")  # a failed action never recovers money
    # The lock must be released even though execution failed, so the case is not stuck.
    assert _is_locked(db, case_id) is False


# ---------------------------------------------------------------------------
# Retry after a failed claim / failed execution
# ---------------------------------------------------------------------------


def test_run_blocked_by_held_lock_then_succeeds_after_release(db):
    case_id = _create_detected_case(db)
    adapter = MockAdapter()
    agent = _agent(db, adapter)

    # Simulate another run holding the lock.
    now = datetime.now(UTC)
    with db.session() as s:
        claimed = CaseRepository(s).try_claim(
            case_id, now=now, stale_before=now - timedelta(minutes=5),
            terminal_statuses=_TERMINAL_VALUES,
        )
    assert claimed is True

    # A run that cannot claim must NOT execute a second intervention.
    agent.process_case(_get(db, case_id))
    assert len(_payment_calls(adapter)) == 0
    assert _get(db, case_id).status is CaseStatus.DETECTED  # untouched

    # Release the lock (as the holder would) and retry — now it proceeds.
    with db.session() as s:
        CaseRepository(s).release(case_id)
    agent.process_case(_get(db, case_id))

    assert len(_payment_calls(adapter)) == 1
    assert _get(db, case_id).status is CaseStatus.RECOVERED
    assert _is_locked(db, case_id) is False


def test_retry_succeeds_after_a_failed_execution_left_the_case_open(db):
    """A transient failure (fails once, then succeeds) recovers on a later run, and the failed
    run never leaves the case locked."""
    case_id = _create_detected_case(db)

    class _FlakyAdapter(MockAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.retry_calls = 0

        def retry_payment(self, case, proposal):  # noqa: ANN001
            self.retry_calls += 1
            if self.retry_calls == 1:
                # First attempt fails technically.
                return MockAdapter(
                    fail_actions=frozenset({ActionType.RETRY_PAYMENT})
                ).retry_payment(case, proposal)
            return super().retry_payment(case, proposal)

    adapter = _FlakyAdapter()
    # First attempt fails, verification never recovers it; the case stays recoverable but is
    # held for a scheduled retry (WAITING) or advanced — either way it must be unlocked after.
    agent = _agent(db, adapter)
    agent.process_case(_get(db, case_id))
    assert _is_locked(db, case_id) is False  # not stuck after the failed attempt

    # Drive any scheduled retry to be due, then re-run until the (now succeeding) retry recovers.
    for _ in range(6):
        case = _get(db, case_id)
        if case.is_terminal:
            break
        if case.next_retry_at is not None:
            with db.session() as s:
                row = CaseRepository(s).get(case_id)
                row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
                CaseRepository(s).save(row)
        agent.process_case(_get(db, case_id))

    final = _get(db, case_id)
    assert _is_locked(db, case_id) is False
    # The case reached a terminal outcome (recovered on the succeeding retry, or bounded-out).
    assert final.is_terminal


# ---------------------------------------------------------------------------
# Stale lock recovery (crash safety)
# ---------------------------------------------------------------------------


def test_stale_lock_is_stolen_so_a_case_is_never_permanently_stuck(db):
    case_id = _create_detected_case(db)
    adapter = MockAdapter()

    # Simulate a crashed run that left an old lock behind (older than the TTL).
    stale = datetime.now(UTC) - timedelta(hours=1)
    with db.session() as s:
        CaseRepository(s).try_claim(
            case_id, now=stale, stale_before=stale - timedelta(minutes=5),
            terminal_statuses=_TERMINAL_VALUES,
        )
    assert _is_locked(db, case_id) is True

    # A new run must steal the abandoned lock and process the case to completion.
    result = _agent(db, adapter).process_case(_get(db, case_id))
    assert result.status is CaseStatus.RECOVERED
    assert len(_payment_calls(adapter)) == 1
    assert _is_locked(db, case_id) is False
