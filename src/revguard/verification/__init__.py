"""RevGuard verification layer (Stage 6/7) — the sole authority on actual recovery.

A technically successful execution never counts as recovered money. The
:class:`OutcomeVerifier` independently confirms the real outcome, and is the only component
allowed to produce a ``RECOVERED`` :class:`~revguard.domain.RecoveryResult` with an amount.
:class:`MockVerifier` provides a deterministic, offline implementation for the demo/tests.
"""

from revguard.verification.verifier import MockVerifier, OutcomeVerifier

__all__ = ["OutcomeVerifier", "MockVerifier"]
