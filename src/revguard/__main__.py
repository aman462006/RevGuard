"""Console entry point.

Sub-commands:

* (no command) — startup health-check, printed as JSON (Phase 0 behaviour). Exit 0 = healthy.
* ``evaluate`` — run the Baseline vs RevGuard batch evaluation on the seeded synthetic dataset
  and print the comparison as a human-readable table (default) or JSON (``--json``).
"""

from __future__ import annotations

import argparse
import sys

from revguard.health import _running_python_supported, check_health


def _run_health() -> int:
    """Print health status as JSON and return a process exit code."""
    status = check_health()
    print(status.model_dump_json(indent=2))
    if not _running_python_supported():
        print(
            f"warning: running on Python {status.python_version}; "
            "project targets >=3.12,<3.14",
            file=sys.stderr,
        )
    return 0


def _run_evaluate(seed: int, as_json: bool) -> int:
    """Run the evaluation and print the report (imported lazily to keep startup light)."""
    from revguard.evaluation import evaluate, render_text

    report = evaluate(seed=seed)
    if as_json:
        print(report.model_dump_json(indent=2))
    else:
        print(render_text(report))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="revguard", description="RevGuard CLI")
    sub = parser.add_subparsers(dest="command")

    ev = sub.add_parser("evaluate", help="run the Baseline vs RevGuard batch evaluation")
    ev.add_argument(
        "--seed",
        type=int,
        default=None,
        help="synthetic data seed (defaults to the shipped seed)",
    )
    ev.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch a sub-command. No command runs the Phase 0 health-check."""
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])

    if args.command == "evaluate":
        from revguard.synthetic import DEFAULT_SEED

        seed = args.seed if args.seed is not None else DEFAULT_SEED
        return _run_evaluate(seed, args.json)

    return _run_health()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
