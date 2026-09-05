"""Detector interface and shared, side-effect-free helpers (Stage 1).

Detectors are **deterministic**: pure functions over a set of :class:`Event` objects that
conclude *that* revenue is at risk by emitting a :class:`RevenueRiskSignal` (or ``None``).
They contain no LLM/API/DB calls and never choose or execute a recovery action — that is
the job of later pipeline stages.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

from revguard.domain import (
    Currency,
    Event,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


@runtime_checkable
class Detector(Protocol):
    """A deterministic revenue-risk detector for a single workflow."""

    workflow: WorkflowType

    def detect(self, events: Sequence[Event]) -> RevenueRiskSignal | None:
        """Inspect events for one subject and return a signal, or ``None`` if no risk."""
        ...


# Severity ordering, lowest → highest. Used to bump/reduce a computed level.
RISK_ORDER: tuple[RiskLevel, ...] = (
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.CRITICAL,
)


def adjust_risk(level: RiskLevel, steps: int) -> RiskLevel:
    """Move ``level`` up (positive) or down (negative) the severity scale, clamped."""
    idx = RISK_ORDER.index(level) + steps
    idx = max(0, min(len(RISK_ORDER) - 1, idx))
    return RISK_ORDER[idx]


def sole_currency(events: Iterable[Event]) -> Currency | None:
    """Return the single currency shared by events, or ``None`` if absent/mixed.

    Detectors need one unambiguous currency to state an amount at risk; a mixed or empty
    group is treated as "cannot assess" (no signal) rather than guessed.
    """
    currencies = {e.currency for e in events if e.currency is not None}
    if len(currencies) == 1:
        return next(iter(currencies))
    return None


def latest(events: Sequence[Event]) -> Event:
    """The most recent event by ``occurred_at`` (stable for ties on input order)."""
    return max(events, key=lambda e: e.occurred_at)


def has_success_after(events: Iterable[Event], reference: Event, *, event_type) -> bool:
    """Whether any event of ``event_type`` occurred at/after ``reference`` (recovery)."""
    return any(
        e.event_type == event_type and e.occurred_at >= reference.occurred_at
        for e in events
    )


__all__ = [
    "Detector",
    "RISK_ORDER",
    "adjust_risk",
    "sole_currency",
    "latest",
    "has_success_after",
]
