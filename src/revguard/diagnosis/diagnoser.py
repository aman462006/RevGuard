"""The provider-agnostic diagnosis interface (Stage 3).

A :class:`Diagnoser` receives a read-only :class:`RecoveryCase` and returns exactly one
:class:`ActionProposal` — a **recommendation only**. Per AGENT_SPEC, this layer depends on
nothing but the domain models: it holds no reference to executors, repositories/DB, the
policy engine, or payment SDKs, so the AI structurally cannot execute anything or bypass the
policy gate.

:func:`safe_fallback_proposal` gives every provider a domain-valid, execution-safe result to
return when it cannot produce a trustworthy proposal — it recommends escalation, which the
deterministic policy engine will route to a human. A malformed/unavailable AI response thus
never reaches the executor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from revguard.domain import ActionProposal, ActionType, RecoveryCase


class Diagnoser(ABC):
    """Interface every AI provider implements. Provider-agnostic and side-effect free."""

    #: Human-readable provider name (for auditable logging; never secrets).
    name: str = "diagnoser"

    @abstractmethod
    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        """Analyse the case and return a single recommended :class:`ActionProposal`."""
        raise NotImplementedError


def safe_fallback_proposal(
    case: RecoveryCase,
    *,
    reason: str,
    confidence: float = 0.0,
    failure_kind: str | None = None,
) -> ActionProposal:
    """A domain-valid, execution-safe proposal used when diagnosis cannot be trusted.

    It recommends escalation (never an executable action), so the policy engine handles it
    as ESCALATE and no side effect is ever produced from a failed diagnosis. ``failure_kind``
    classifies *why* (e.g. ``quota`` / ``authentication`` / ``timeout``) and is recorded in the
    proposal evidence so the audit trail and case escalation reason can surface it — never a
    credential or raw provider payload.
    """
    evidence: dict[str, str | bool] = {"fallback": True}
    if failure_kind is not None:
        evidence["failure_kind"] = failure_kind
    return ActionProposal(
        case_id=case.case_id,
        action_type=ActionType.RECOMMEND_ESCALATION,
        rationale=reason,
        confidence=confidence,
        evidence=evidence,
    )


__all__ = ["Diagnoser", "safe_fallback_proposal"]
