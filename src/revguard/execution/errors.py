"""Execution-layer errors (Phase 6).

Kept in their own module so both :mod:`revguard.execution.adapter` and
:mod:`revguard.execution.executor` can raise/catch them without an import cycle.
"""

from __future__ import annotations


class ExecutionError(Exception):
    """Base class for all execution-layer failures."""


class ExecutionRejected(ExecutionError):
    """The executor refused to run — e.g. the decision was not APPROVE, or the APPROVE
    did not reference the exact action/case being executed. No side effect occurred."""


class UnsupportedActionError(ExecutionError):
    """An adapter was asked to perform an action outside its closed whitelist.

    Guards against arbitrary tool/function execution: an adapter can only ever perform
    the finite set of permitted :class:`~revguard.domain.ActionType` recovery actions."""


__all__ = ["ExecutionError", "ExecutionRejected", "UnsupportedActionError"]
