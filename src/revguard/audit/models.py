"""Audit domain model — an immutable, append-only record of a workflow transition.

Pure Pydantic (no SQLAlchemy). Every important pipeline transition can be captured as an
:class:`AuditEntry`: who acted (``actor``), at which stage, what action/decision resulted,
and the structured ``details``. ``seq`` is assigned by the store on write (append order).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from revguard.domain import new_id, utcnow


class AuditActor(StrEnum):
    """The source responsible for an audited transition."""

    SYSTEM = "system"
    AI = "ai"
    POLICY = "policy"
    EXECUTOR = "executor"
    HUMAN = "human"


class AuditStage(StrEnum):
    """The pipeline stage / event type an entry records."""

    DETECTION = "detection"
    CASE_CREATED = "case_created"
    DIAGNOSIS = "diagnosis"
    POLICY_DECISION = "policy_decision"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    ESCALATION = "escalation"
    STOP = "stop"
    RECOVERY_RESULT = "recovery_result"
    STATUS_CHANGE = "status_change"
    NOTE = "note"


class AuditEntry(BaseModel):
    """One append-only audit record. Immutable once created."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(default_factory=lambda: new_id("aud"))
    case_id: str | None = None
    recorded_at: AwareDatetime = Field(default_factory=utcnow)

    stage: AuditStage
    actor: AuditActor
    # The action taken or decision reached, where applicable (e.g. "retry_payment",
    # "APPROVE", "ESCALATE").
    action: str | None = None

    details: dict[str, Any] = Field(default_factory=dict)
    # Ties related entries together (e.g. an event id, decision id, or trace id).
    correlation_id: str | None = None

    # Assigned by the append-only store on persist; None before it is written.
    seq: int | None = None


__all__ = ["AuditEntry", "AuditActor", "AuditStage"]
