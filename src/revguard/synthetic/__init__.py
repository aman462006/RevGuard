"""RevGuard synthetic data generation (Phase 2 support).

Deterministic, seed-reproducible generation of realistic :class:`~revguard.domain.Event`
scenarios spanning all four workflows — normal, clear-risk, boundary, multi-severity, and
no-signal cases. Used by detector tests now and by the batch evaluator later. This is a
support module (mirrored by ``data/synthetic/`` output + a future ``scripts`` CLI); it adds
no runtime pipeline behavior.
"""

from revguard.synthetic.generator import (
    DEFAULT_SEED,
    Scenario,
    build_catalog,
    generate_events,
)

__all__ = ["DEFAULT_SEED", "Scenario", "build_catalog", "generate_events"]
