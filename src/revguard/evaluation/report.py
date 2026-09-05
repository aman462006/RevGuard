"""Evaluation report: one object rendered as both machine- and human-readable output.

:class:`EvaluationReport` is the machine-readable artefact (a Pydantic model → JSON via
``model_dump_json``). :func:`render_text` turns the same data into a compact console table so
a human can read the Baseline vs RevGuard comparison at a glance. Rendering is pure and does
no I/O, so callers decide where the output goes.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from revguard.evaluation.runner import run_evaluation
from revguard.metrics import BatchMetrics, Comparison
from revguard.persistence import Database
from revguard.synthetic import DEFAULT_SEED


class EvaluationReport(BaseModel):
    """The full, machine-readable evaluation result for one seeded batch."""

    seed: int
    comparison: Comparison


def evaluate(
    *,
    seed: int = DEFAULT_SEED,
    baseline_db: Database | None = None,
    revguard_db: Database | None = None,
) -> EvaluationReport:
    """Run the full Baseline-vs-RevGuard evaluation and wrap it in a report."""
    comparison = run_evaluation(seed=seed, baseline_db=baseline_db, revguard_db=revguard_db)
    return EvaluationReport(seed=seed, comparison=comparison)


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _pct(rate: Decimal) -> str:
    return f"{rate * 100:.2f}%"


def _row(label: str, baseline: str, revguard: str, delta: str = "") -> str:
    return f"  {label:<22}{baseline:>16}{revguard:>16}{delta:>14}"


def _signed_money(value: Decimal) -> str:
    return f"{'+' if value >= 0 else '-'}{_money(abs(value))}"


def _signed_int(value: int) -> str:
    return f"{value:+d}"


def render_text(report: EvaluationReport) -> str:
    """Render a human-readable comparison table for ``report``."""
    c: Comparison = report.comparison
    b: BatchMetrics = c.baseline
    r: BatchMetrics = c.revguard
    d = c.delta

    lines: list[str] = []
    lines.append("=" * 68)
    lines.append(f"RevGuard evaluation - seed {report.seed}")
    lines.append("=" * 68)
    lines.append(_row("metric", "baseline", "revguard", "delta"))
    lines.append("  " + "-" * 66)
    lines.append(_row("cases", str(b.total_cases), str(r.total_cases)))
    lines.append(_row("revenue at risk", _money(b.revenue_at_risk), _money(r.revenue_at_risk)))
    lines.append(
        _row(
            "revenue recovered",
            _money(b.recovered_amount),
            _money(r.recovered_amount),
            _signed_money(d.revenue_recovered_delta),
        )
    )
    lines.append(
        _row(
            "unresolved amount",
            _money(b.unresolved_amount),
            _money(r.unresolved_amount),
        )
    )
    lines.append(
        _row(
            "recovery rate",
            _pct(b.recovery_rate),
            _pct(r.recovery_rate),
            _pct(d.recovery_rate_delta),
        )
    )
    lines.append(
        _row(
            "recovered cases",
            str(b.recovered),
            str(r.recovered),
            _signed_int(d.recovered_cases_delta),
        )
    )
    lines.append(
        _row("escalated", str(b.escalated), str(r.escalated), _signed_int(d.escalated_delta))
    )
    lines.append(
        _row("stopped", str(b.stopped), str(r.stopped), _signed_int(d.stopped_delta))
    )
    lines.append(_row("failed", str(b.failed), str(r.failed), _signed_int(d.failed_delta)))
    lines.append(_row("open (non-terminal)", str(b.open_cases), str(r.open_cases)))

    lines.append("")
    lines.append("  recovery rate by workflow (baseline -> revguard)")
    lines.append("  " + "-" * 66)
    for workflow, r_wf in r.by_workflow.items():
        b_wf = b.by_workflow.get(workflow)
        b_rate = _pct(b_wf.recovery_rate) if b_wf else "n/a"
        lines.append(
            f"  {workflow.value:<24}{b_rate:>12} -> {_pct(r_wf.recovery_rate):>10}"
            f"   ({r_wf.recovered}/{r_wf.total_cases} cases)"
        )

    lines.append("=" * 68)
    lines.append(
        "Comparison is as-measured on the identical batch; no claim beyond the numbers."
    )
    return "\n".join(lines)


__all__ = ["EvaluationReport", "evaluate", "render_text"]
