"""End-to-end batch evaluation: Baseline vs RevGuard on one seeded synthetic batch.

Uses the real RecoveryAgent for both strategies with the deterministic offline
GroundTruthVerifier (no network, no real AI). Each strategy runs against its own in-memory
database created inside the runner.
"""

from __future__ import annotations

from decimal import Decimal

from revguard.domain import CaseStatus, WorkflowType
from revguard.evaluation import evaluate, render_text, run_evaluation, run_strategy


def test_both_strategies_run_on_the_identical_batch():
    baseline = run_strategy("baseline")
    revguard = run_strategy("revguard")
    # Same detector + same seed => the same batch of cases and the same revenue at risk.
    assert baseline.metrics.total_cases == revguard.metrics.total_cases > 0
    assert baseline.metrics.revenue_at_risk == revguard.metrics.revenue_at_risk


def test_evaluation_is_deterministic():
    a = evaluate()
    b = evaluate()
    assert a.model_dump_json() == b.model_dump_json()


def test_recovered_money_is_verified_only_and_not_double_counted():
    run = run_strategy("revguard")
    # recovered_amount is exactly the sum of per-case verified recoveries, each counted once.
    expected = sum((c.amount_recovered for c in run.cases), Decimal("0"))
    assert run.metrics.recovered_amount == expected
    # Only RECOVERED cases may carry a recovered amount.
    for case in run.cases:
        if case.amount_recovered > 0:
            assert case.status is CaseStatus.RECOVERED
    # Recovery can never exceed the money that was at risk.
    assert run.metrics.recovered_amount <= run.metrics.revenue_at_risk


def test_comparison_covers_all_four_workflows():
    comparison = run_evaluation()
    assert set(comparison.revguard.by_workflow) == set(WorkflowType)
    assert set(comparison.baseline.by_workflow) == set(WorkflowType)


def test_revguard_recovers_at_least_as_much_as_baseline():
    # Ground truth is identical for both; RevGuard's first action matches the baseline's and
    # it may take further distinct actions, so it can never recover *less* on the same batch.
    comparison = run_evaluation()
    assert comparison.revguard.recovered_amount >= comparison.baseline.recovered_amount
    assert comparison.delta.revenue_recovered_delta >= 0
    assert comparison.revguard.recovered >= comparison.baseline.recovered


def test_metrics_conservation_holds():
    # No money is invented or lost: recovered + unresolved == at risk, for each strategy.
    comparison = run_evaluation()
    for m in (comparison.baseline, comparison.revguard):
        assert m.recovered_amount + m.unresolved_amount == m.revenue_at_risk
        assert m.unresolved_cases == m.total_cases - m.recovered


def test_render_text_is_ascii_and_mentions_both_strategies():
    text = render_text(evaluate())
    assert text.isascii()  # safe on a Windows cp1252 console
    assert "baseline" in text and "revguard" in text
