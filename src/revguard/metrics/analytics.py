"""Recovery analytics — deterministic operational visibility over persisted RevGuard data.

This module answers three operator questions, using **only** stored cases and the append-only
audit log — never the AI, never any live call, and never a new datastore:

* **Which interventions actually recover money?** :func:`compute_recovery_analytics` reconstructs
  per-action effectiveness from the audit trail (attempts, verified recoveries, recovered amount,
  and recovery rate), attributing a case's verified recovery to the action that achieved it.
* **What are the outcomes?** RECOVERED / ESCALATED / STOPPED / FAILED / still-pending counts and
  amounts, taken straight from the cases (``amount_recovered`` is verified-only by construction).
* **Is recovery improving or declining?** A day-bucketed timeline of recovered amount, recovery
  count, and attempts, from audit ``recorded_at`` timestamps.

Everything here is a pure function: the same cases + audit entries always yield the same result.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel

from revguard.audit import AuditEntry, AuditStage
from revguard.domain import CaseStatus, RecoveryCase

_ZERO = Decimal("0")
_RATE_Q = Decimal("0.0001")


def _rate(part: Decimal | int, whole: Decimal | int) -> Decimal:
    """A fraction in ``[0, 1]`` as a 4-dp Decimal (0 when the denominator is 0)."""
    whole = Decimal(whole)
    if whole <= _ZERO:
        return Decimal("0.0000")
    return (Decimal(part) / whole).quantize(_RATE_Q)


def _amount(details: dict, key: str = "amount_recovered") -> Decimal:
    """Parse a Decimal amount from an audit ``details`` value, defaulting to 0."""
    raw = details.get(key)
    try:
        return Decimal(str(raw)) if raw is not None else _ZERO
    except (ValueError, ArithmeticError):
        return _ZERO


class ActionEffectiveness(BaseModel):
    """How effective one intervention/action type is at actually recovering money."""

    action: str
    attempts: int
    recoveries: int
    recovered_amount: Decimal
    recovery_rate: Decimal  # recoveries / attempts, in [0, 1]


class OutcomeBreakdown(BaseModel):
    """The main case outcomes: RECOVERED / ESCALATED / STOPPED / FAILED / still-pending."""

    recovered: int
    escalated: int
    stopped: int
    failed: int
    pending: int  # non-terminal (open) — neither recovered nor closed
    recovered_amount: Decimal
    at_risk_amount: Decimal
    pending_amount: Decimal  # unresolved amount still open


class ActivityBucket(BaseModel):
    """Recovery activity for one calendar day (UTC), for the time-based trend view."""

    date: str  # ISO date (YYYY-MM-DD), UTC
    recovered_amount: Decimal
    recovered_count: int
    attempts: int


class RecoveryAnalytics(BaseModel):
    """The full analytics snapshot for the operator dashboard."""

    total_cases: int
    total_attempts: int
    total_recovered_amount: Decimal
    overall_recovery_rate: Decimal  # recovered_amount / at_risk_amount
    by_action: list[ActionEffectiveness]
    outcomes: OutcomeBreakdown
    timeline: list[ActivityBucket]


def _compute_outcomes(cases: list[RecoveryCase]) -> OutcomeBreakdown:
    counts = {
        CaseStatus.RECOVERED: 0,
        CaseStatus.ESCALATED: 0,
        CaseStatus.STOPPED: 0,
        CaseStatus.FAILED: 0,
    }
    pending = 0
    recovered_amount = _ZERO
    at_risk = _ZERO
    pending_amount = _ZERO
    for case in cases:
        at_risk += case.amount_at_risk
        recovered_amount += case.amount_recovered  # verified-only, set by the orchestrator
        if case.status in counts:
            counts[case.status] += 1
        else:
            pending += 1
            pending_amount += case.amount_at_risk - case.amount_recovered
    return OutcomeBreakdown(
        recovered=counts[CaseStatus.RECOVERED],
        escalated=counts[CaseStatus.ESCALATED],
        stopped=counts[CaseStatus.STOPPED],
        failed=counts[CaseStatus.FAILED],
        pending=pending,
        recovered_amount=recovered_amount,
        at_risk_amount=at_risk,
        pending_amount=pending_amount,
    )


def _executed(entry: AuditEntry) -> bool:
    """True for an audit entry that records a real (non-rejected) action execution."""
    return (
        entry.stage is AuditStage.EXECUTION
        and bool(entry.action)
        and not entry.details.get("rejected")
    )


def _compute_by_action(
    entries_by_case: dict[str | None, list[AuditEntry]],
) -> list[ActionEffectiveness]:
    """Per-action attempts, verified recoveries, and recovered amount from the audit trail.

    Within each case's ordered entries, a verified ``recovery_result`` is attributed to the most
    recently executed action — the intervention that actually collected the money.
    """
    attempts: dict[str, int] = defaultdict(int)
    recoveries: dict[str, int] = defaultdict(int)
    amount: dict[str, Decimal] = defaultdict(lambda: _ZERO)

    for entries in entries_by_case.values():
        last_action: str | None = None
        for e in entries:
            if _executed(e):
                assert e.action is not None
                attempts[e.action] += 1
                last_action = e.action
            elif e.stage is AuditStage.RECOVERY_RESULT and last_action is not None:
                recoveries[last_action] += 1
                amount[last_action] += _amount(e.details)

    rows = [
        ActionEffectiveness(
            action=action,
            attempts=attempts[action],
            recoveries=recoveries.get(action, 0),
            recovered_amount=amount.get(action, _ZERO),
            recovery_rate=_rate(recoveries.get(action, 0), attempts[action]),
        )
        for action in attempts
    ]
    # Deterministic ordering: most money recovered first, then most attempts, then name.
    rows.sort(key=lambda r: (-r.recovered_amount, -r.attempts, r.action))
    return rows


def _compute_timeline(entries: list[AuditEntry]) -> list[ActivityBucket]:
    """Day-bucketed recovered amount, recovery count, and attempts (UTC calendar days)."""
    recovered_amount: dict[str, Decimal] = defaultdict(lambda: _ZERO)
    recovered_count: dict[str, int] = defaultdict(int)
    attempts: dict[str, int] = defaultdict(int)
    days: set[str] = set()

    for e in entries:
        day = e.recorded_at.date().isoformat()
        if _executed(e):
            attempts[day] += 1
            days.add(day)
        elif e.stage is AuditStage.RECOVERY_RESULT:
            recovered_amount[day] += _amount(e.details)
            recovered_count[day] += 1
            days.add(day)

    return [
        ActivityBucket(
            date=day,
            recovered_amount=recovered_amount.get(day, _ZERO),
            recovered_count=recovered_count.get(day, 0),
            attempts=attempts.get(day, 0),
        )
        for day in sorted(days)
    ]


def compute_recovery_analytics(
    cases: Iterable[RecoveryCase], audit_entries: Iterable[AuditEntry]
) -> RecoveryAnalytics:
    """Compute the deterministic analytics snapshot from persisted cases + audit entries."""
    cases = list(cases)
    entries = list(audit_entries)

    entries_by_case: dict[str | None, list[AuditEntry]] = defaultdict(list)
    for e in entries:
        entries_by_case[e.case_id].append(e)
    # Preserve append order within each case for correct recovery attribution.
    for case_entries in entries_by_case.values():
        case_entries.sort(key=lambda e: (e.seq is None, e.seq or 0))

    by_action = _compute_by_action(entries_by_case)
    outcomes = _compute_outcomes(cases)
    timeline = _compute_timeline(entries)

    return RecoveryAnalytics(
        total_cases=len(cases),
        total_attempts=sum(r.attempts for r in by_action),
        total_recovered_amount=outcomes.recovered_amount,
        overall_recovery_rate=_rate(outcomes.recovered_amount, outcomes.at_risk_amount),
        by_action=by_action,
        outcomes=outcomes,
        timeline=timeline,
    )


__all__ = [
    "ActionEffectiveness",
    "OutcomeBreakdown",
    "ActivityBucket",
    "RecoveryAnalytics",
    "compute_recovery_analytics",
]
