"""Batch evaluation runners (EVALUATION_SPEC §6, §7).

Runs a strategy end to end over the seeded synthetic batch and computes its metrics, then
compares Baseline vs RevGuard on the **same** batch. Both strategies reuse the existing
:class:`~revguard.orchestrator.RecoveryAgent` and the same offline
:class:`~revguard.evaluation.ground_truth.GroundTruthVerifier`, so the only variable is the
diagnosis strategy. Each strategy runs against its own fresh database (so the two runs never
share or deduplicate each other's cases), and the batch is regenerated from the seed, making
the whole evaluation deterministic and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from revguard.diagnosis import MockDiagnoser
from revguard.domain import RecoveryCase
from revguard.evaluation.baseline import RuleOnlyDiagnoser
from revguard.evaluation.ground_truth import GroundTruthVerifier
from revguard.metrics import BatchMetrics, Comparison, compare, compute_metrics
from revguard.orchestrator import RecoveryAgent
from revguard.persistence import CaseRepository, Database
from revguard.synthetic import DEFAULT_SEED, generate_events

Strategy = Literal["baseline", "revguard"]


@dataclass(frozen=True)
class StrategyRun:
    """The outcome of running one strategy over a batch: its final cases and metrics."""

    strategy: str
    cases: list[RecoveryCase]
    metrics: BatchMetrics


def _new_memory_db() -> Database:
    db = Database("sqlite:///:memory:")
    db.create_all()
    return db


def _diagnoser(strategy: Strategy):
    if strategy == "baseline":
        return RuleOnlyDiagnoser()
    # RevGuard uses the deterministic offline diagnoser so the evaluation is reproducible and
    # needs no API key; a real provider can be swapped in behind the same interface.
    return MockDiagnoser()


def run_strategy(
    strategy: Strategy,
    *,
    seed: int = DEFAULT_SEED,
    database: Database | None = None,
) -> StrategyRun:
    """Run ``strategy`` over the seeded batch and return its final cases + metrics."""
    db = database or _new_memory_db()
    agent = RecoveryAgent(
        db,
        diagnoser=_diagnoser(strategy),
        verifier=GroundTruthVerifier(seed=seed),
    )
    agent.process_events(generate_events(seed))
    with db.session() as session:
        cases = CaseRepository(session).list_all()
    return StrategyRun(strategy, cases, compute_metrics(strategy, cases))


def run_evaluation(
    *,
    seed: int = DEFAULT_SEED,
    baseline_db: Database | None = None,
    revguard_db: Database | None = None,
) -> Comparison:
    """Run both strategies on the identical batch and return the side-by-side comparison."""
    baseline = run_strategy("baseline", seed=seed, database=baseline_db)
    revguard = run_strategy("revguard", seed=seed, database=revguard_db)
    return compare(baseline.metrics, revguard.metrics)


__all__ = ["Strategy", "StrategyRun", "run_strategy", "run_evaluation"]
