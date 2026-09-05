"""RevGuard evaluation package — batch runner, offline ground truth, and reporting."""

from revguard.evaluation.baseline import RuleOnlyDiagnoser
from revguard.evaluation.ground_truth import (
    GroundTruth,
    GroundTruthVerifier,
    ground_truth_for,
)
from revguard.evaluation.report import EvaluationReport, evaluate, render_text
from revguard.evaluation.runner import (
    Strategy,
    StrategyRun,
    run_evaluation,
    run_strategy,
)

__all__ = [
    "RuleOnlyDiagnoser",
    "GroundTruth",
    "GroundTruthVerifier",
    "ground_truth_for",
    "EvaluationReport",
    "evaluate",
    "render_text",
    "Strategy",
    "StrategyRun",
    "run_evaluation",
    "run_strategy",
]
