"""Batch recovery metrics — pure, deterministic aggregation over recovery cases (Stage 8).

Given the cases a strategy produced, :func:`compute_metrics` computes the money-oriented and
workflow-oriented figures the evaluation reports, and :func:`compare` places two strategies
side by side on the same batch. Everything here is a **pure function** over
:class:`~revguard.domain.RecoveryCase` objects:

* money is summed with :class:`~decimal.Decimal` (never float);
* recovered money is taken **only** from ``case.amount_recovered``, which the orchestrator
  sets exclusively from a verified :class:`~revguard.domain.RecoveryResult` — so a technically
  successful action that was never verified contributes nothing;
* each case is counted exactly once (no double counting): ``recovered_amount`` is a straight
  sum over cases and ``unresolved_amount`` is the remainder of ``revenue_at_risk``.
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from pydantic import BaseModel

from revguard.domain import CaseStatus, RecoveryCase, WorkflowType

_ZERO = Decimal("0")
_RATE_Q = Decimal("0.0001")


def _rate(part: Decimal, whole: Decimal) -> Decimal:
    """Recovered fraction in ``[0, 1]`` as a 4-dp Decimal (0 when nothing is at risk)."""
    if whole <= _ZERO:
        return Decimal("0.0000")
    return (part / whole).quantize(_RATE_Q)


class WorkflowMetrics(BaseModel):
    """Metrics for a single workflow within a batch."""

    workflow: WorkflowType
    total_cases: int
    revenue_at_risk: Decimal
    recovered_amount: Decimal
    unresolved_amount: Decimal
    recovery_rate: Decimal
    recovered: int
    escalated: int
    stopped: int
    failed: int
    open_cases: int  # non-terminal (e.g. WAITING) — neither recovered nor closed


class BatchMetrics(BaseModel):
    """Aggregate metrics for one strategy over one batch of cases."""

    strategy: str
    total_cases: int
    revenue_at_risk: Decimal
    recovered_amount: Decimal
    unresolved_amount: Decimal
    recovery_rate: Decimal
    recovered: int
    escalated: int
    stopped: int
    failed: int
    open_cases: int
    unresolved_cases: int  # every case that did not end RECOVERED
    by_workflow: dict[WorkflowType, WorkflowMetrics]


class StrategyDelta(BaseModel):
    """RevGuard-minus-baseline differences on the same batch."""

    revenue_recovered_delta: Decimal
    recovery_rate_delta: Decimal
    recovered_cases_delta: int
    escalated_delta: int
    stopped_delta: int
    failed_delta: int


class Comparison(BaseModel):
    """Two strategies measured on the identical batch, plus their delta."""

    baseline: BatchMetrics
    revguard: BatchMetrics
    delta: StrategyDelta


def _accumulate(cases: list[RecoveryCase]) -> dict:
    """Sum the raw figures for a list of cases (one traversal, each case once)."""
    revenue_at_risk = _ZERO
    recovered_amount = _ZERO
    counts = {
        CaseStatus.RECOVERED: 0,
        CaseStatus.ESCALATED: 0,
        CaseStatus.STOPPED: 0,
        CaseStatus.FAILED: 0,
    }
    open_cases = 0
    for case in cases:
        revenue_at_risk += case.amount_at_risk
        recovered_amount += case.amount_recovered  # verified-only, set by the orchestrator
        if case.status in counts:
            counts[case.status] += 1
        else:
            open_cases += 1  # non-terminal state (e.g. WAITING/ANALYZING)
    return {
        "total_cases": len(cases),
        "revenue_at_risk": revenue_at_risk,
        "recovered_amount": recovered_amount,
        "unresolved_amount": revenue_at_risk - recovered_amount,
        "recovery_rate": _rate(recovered_amount, revenue_at_risk),
        "recovered": counts[CaseStatus.RECOVERED],
        "escalated": counts[CaseStatus.ESCALATED],
        "stopped": counts[CaseStatus.STOPPED],
        "failed": counts[CaseStatus.FAILED],
        "open_cases": open_cases,
    }


def compute_metrics(strategy: str, cases: Iterable[RecoveryCase]) -> BatchMetrics:
    """Compute the full metric set for ``strategy`` over ``cases``.

    Deterministic and side-effect free: the same cases always yield the same metrics.
    """
    cases = list(cases)
    totals = _accumulate(cases)

    by_workflow: dict[WorkflowType, WorkflowMetrics] = {}
    for workflow in WorkflowType:
        wf_cases = [c for c in cases if c.case_type is workflow]
        if not wf_cases:
            continue
        agg = _accumulate(wf_cases)
        by_workflow[workflow] = WorkflowMetrics(workflow=workflow, **agg)

    return BatchMetrics(
        strategy=strategy,
        unresolved_cases=totals["total_cases"] - totals["recovered"],
        by_workflow=by_workflow,
        **totals,
    )


def compare(baseline: BatchMetrics, revguard: BatchMetrics) -> Comparison:
    """Place ``baseline`` and ``revguard`` side by side and compute RevGuard-minus-baseline."""
    delta = StrategyDelta(
        revenue_recovered_delta=revguard.recovered_amount - baseline.recovered_amount,
        recovery_rate_delta=(revguard.recovery_rate - baseline.recovery_rate),
        recovered_cases_delta=revguard.recovered - baseline.recovered,
        escalated_delta=revguard.escalated - baseline.escalated,
        stopped_delta=revguard.stopped - baseline.stopped,
        failed_delta=revguard.failed - baseline.failed,
    )
    return Comparison(baseline=baseline, revguard=revguard, delta=delta)


__all__ = [
    "WorkflowMetrics",
    "BatchMetrics",
    "StrategyDelta",
    "Comparison",
    "compute_metrics",
    "compare",
]
