"""Phase 0 smoke tests: the package imports and exposes a version."""

from __future__ import annotations

import importlib


def test_package_imports():
    module = importlib.import_module("revguard")
    assert module is not None


def test_version_present():
    import revguard

    assert isinstance(revguard.__version__, str)
    assert revguard.__version__


def test_scaffold_subpackages_import():
    # Every architectural package from ARCHITECTURE.md should be importable.
    for name in [
        "revguard.domain",
        "revguard.detection",
        "revguard.diagnosis",
        "revguard.policy",
        "revguard.execution",
        "revguard.verification",
        "revguard.orchestrator",
        "revguard.audit",
        "revguard.persistence",
        "revguard.metrics",
        "revguard.evaluation",
        "revguard.integrations",
        "revguard.integrations.razorpay",
        "revguard.api",
    ]:
        __import__(name)
