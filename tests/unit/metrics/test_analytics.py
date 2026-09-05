"""Recovery analytics: action-level effectiveness, terminal outcomes, and time aggregation.

Everything is computed deterministically from cases + audit entries only (no AI, no I/O)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from revguard.audit import AuditActor, AuditEntry, AuditStage
from revguard.domain import (
    CaseStatus,
    Currency,
    RecoveryCase,
    RevenueRiskSignal,
    RiskLevel,
    StopReason,
    WorkflowType,
    utcnow,
)
from revguard.metrics import compute_recovery_analytics

_SEQ = 0


def _entry(
    *,
    stage: AuditStage,
    case_id: str = "case_1",
    action: str | None = None,
    details: dict | None = None,
    recorded_at: datetime | None = None,
) -> AuditEntry:
    global _SEQ
    _SEQ += 1
    return AuditEntry(
        case_id=case_id,
        stage=stage,
        actor=AuditActor.EXECUTOR if stage is AuditStage.EXECUTION else AuditActor.SYSTEM,
        action=action,
        details=details or {},
        recorded_at=recorded_at or utcnow(),
        seq=_SEQ,
    )


def _execution(action: str, *, case_id: str = "case_1", rejected: bool = False,
               recorded_at: datetime | None = None) -> AuditEntry:
    return _entry(
        stage=AuditStage.EXECUTION,
        case_id=case_id,
        action=action,
        details={"rejected": rejected} if rejected else {"simulated": True},
        recorded_at=recorded_at,
    )


def _recovery(amount: str, *, case_id: str = "case_1",
              recorded_at: datetime | None = None) -> AuditEntry:
    return _entry(
        stage=AuditStage.RECOVERY_RESULT,
        case_id=case_id,
        action="recovered",
        details={"amount_recovered": amount, "currency": "INR"},
        recorded_at=recorded_at,
    )


def _case(
    *,
    amount: Decimal = Decimal("1000.00"),
    recovered: Decimal = Decimal("0"),
    status: CaseStatus = CaseStatus.ACTION_PENDING,
    workflow: WorkflowType = WorkflowType.FAILED_SUBSCRIPTION,
) -> RecoveryCase:
    signal = RevenueRiskSignal(
        signal_type=workflow,
        risk_level=RiskLevel.HIGH,
        amount_at_risk=amount,
        currency=Currency.INR,
        source_event_ids=["evt_1"],
    )
    extra: dict = {}
    if status is CaseStatus.STOPPED:
        extra = dict(stopped_at=utcnow(), stop_reason=StopReason.MAX_ATTEMPTS_REACHED)
    elif status is CaseStatus.ESCALATED:
        extra = dict(escalated_at=utcnow(), escalation_reason="human review")
    return RecoveryCase(
        case_type=workflow,
        signal=signal,
        amount_at_risk=amount,
        currency=Currency.INR,
        amount_recovered=recovered,
        status=status,
        **extra,
    )


# -- empty ------------------------------------------------------------------------------


def test_empty_analytics_is_all_zero():
    a = compute_recovery_analytics([], [])
    assert a.total_cases == 0
    assert a.total_attempts == 0
    assert a.total_recovered_amount == Decimal("0")
    assert a.overall_recovery_rate == Decimal("0.0000")
    assert a.by_action == []
    assert a.timeline == []
    o = a.outcomes
    assert (o.recovered, o.escalated, o.stopped, o.failed, o.pending) == (0, 0, 0, 0, 0)


# -- action-level effectiveness ---------------------------------------------------------


def test_action_effectiveness_counts_attempts_recoveries_and_amount():
    case = _case(amount=Decimal("1000.00"), recovered=Decimal("1000.00"),
                 status=CaseStatus.RECOVERED)
    entries = [_execution("retry_payment"), _recovery("1000.00")]
    a = compute_recovery_analytics([case], entries)
    assert len(a.by_action) == 1
    row = a.by_action[0]
    assert row.action == "retry_payment"
    assert row.attempts == 1
    assert row.recoveries == 1
    assert row.recovered_amount == Decimal("1000.00")
    assert row.recovery_rate == Decimal("1.0000")
    assert a.total_attempts == 1


def test_attempt_without_recovery_has_zero_rate():
    case = _case(status=CaseStatus.ESCALATED)
    a = compute_recovery_analytics([case], [_execution("send_reminder")])
    row = next(r for r in a.by_action if r.action == "send_reminder")
    assert row.attempts == 1
    assert row.recoveries == 0
    assert row.recovered_amount == Decimal("0")
    assert row.recovery_rate == Decimal("0.0000")


def test_recovery_is_attributed_to_the_last_executed_action():
    # A first attempt failed, a second action succeeded — the money is credited to the action
    # that actually recovered it, not the earlier one.
    case = _case(amount=Decimal("2000.00"), recovered=Decimal("2000.00"),
                 status=CaseStatus.RECOVERED)
    entries = [
        _execution("retry_payment"),
        _execution("create_payment_link"),
        _recovery("2000.00"),
    ]
    a = compute_recovery_analytics([case], entries)
    by = {r.action: r for r in a.by_action}
    assert by["retry_payment"].attempts == 1
    assert by["retry_payment"].recoveries == 0
    assert by["retry_payment"].recovered_amount == Decimal("0")
    assert by["create_payment_link"].recoveries == 1
    assert by["create_payment_link"].recovered_amount == Decimal("2000.00")


def test_rejected_executions_are_not_counted_as_attempts():
    case = _case(status=CaseStatus.ESCALATED)
    entries = [_execution("send_reminder", rejected=True)]
    a = compute_recovery_analytics([case], entries)
    assert a.by_action == []
    assert a.total_attempts == 0


def test_by_action_rows_sorted_by_recovered_amount_desc():
    case = _case(amount=Decimal("5000.00"), recovered=Decimal("5000.00"),
                 status=CaseStatus.RECOVERED)
    entries = [
        _execution("send_reminder", case_id="c1"),  # 0 recovered
        _execution("retry_payment", case_id="c2"),
        _recovery("5000.00", case_id="c2"),
    ]
    a = compute_recovery_analytics([case], entries)
    assert [r.action for r in a.by_action] == ["retry_payment", "send_reminder"]


# -- terminal outcomes ------------------------------------------------------------------


def test_outcome_breakdown_counts_and_amounts():
    cases = [
        _case(amount=Decimal("1000.00"), recovered=Decimal("1000.00"),
              status=CaseStatus.RECOVERED),
        _case(amount=Decimal("2000.00"), status=CaseStatus.ESCALATED),
        _case(amount=Decimal("500.00"), status=CaseStatus.STOPPED),
        _case(amount=Decimal("300.00"), status=CaseStatus.FAILED),
        _case(amount=Decimal("800.00"), status=CaseStatus.WAITING),  # pending/open
    ]
    a = compute_recovery_analytics(cases, [])
    o = a.outcomes
    assert (o.recovered, o.escalated, o.stopped, o.failed, o.pending) == (1, 1, 1, 1, 1)
    assert o.recovered_amount == Decimal("1000.00")
    assert o.at_risk_amount == Decimal("4600.00")
    assert o.pending_amount == Decimal("800.00")  # unresolved of the open case
    assert a.overall_recovery_rate == Decimal("0.2174")  # 1000 / 4600


# -- time aggregation -------------------------------------------------------------------


def test_timeline_buckets_by_day_sorted_ascending():
    d1 = datetime(2026, 3, 1, 10, 0, tzinfo=UTC)
    d1b = datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
    d2 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
    entries = [
        _execution("retry_payment", recorded_at=d1),
        _recovery("1000.00", recorded_at=d1b),
        _execution("create_payment_link", case_id="c2", recorded_at=d2),
        _recovery("2500.00", case_id="c2", recorded_at=d2),
    ]
    a = compute_recovery_analytics([], entries)
    assert [b.date for b in a.timeline] == ["2026-03-01", "2026-03-02"]
    day1, day2 = a.timeline
    assert day1.recovered_amount == Decimal("1000.00")
    assert day1.recovered_count == 1
    assert day1.attempts == 1
    assert day2.recovered_amount == Decimal("2500.00")
    assert day2.attempts == 1
