"""SQLAlchemy ORM tables. This is the ONLY place (with ``audit``) that knows about the DB.

Domain models stay pure Pydantic; repositories map between these rows and domain objects.
Money uses :class:`DecimalText` (exact) and timestamps use :class:`AwareDateTime`
(tz-aware). The audit table is append-only by convention — no repository exposes
update/delete for it — and carries an autoincrement ``seq`` giving a total insertion order.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from revguard.persistence.types import AwareDateTime, DecimalText


class Base(DeclarativeBase):
    """Declarative base holding the shared metadata for all RevGuard tables."""


class CaseRow(Base):
    """Persistent form of a :class:`~revguard.domain.RecoveryCase` (aggregate root).

    The originating signal is stored as a JSON value object embedded in the case row.
    """

    __tablename__ = "recovery_cases"

    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_type: Mapped[str] = mapped_column(String, index=True)
    customer_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)

    amount_at_risk: Mapped[Decimal] = mapped_column(DecimalText)
    currency: Mapped[str] = mapped_column(String)
    amount_recovered: Mapped[Decimal] = mapped_column(DecimalText)

    status: Mapped[str] = mapped_column(String, index=True)

    attempt_count: Mapped[int] = mapped_column(Integer)
    current_step: Mapped[int] = mapped_column(Integer)
    current_action: Mapped[str | None] = mapped_column(String, nullable=True)
    next_action: Mapped[str | None] = mapped_column(String, nullable=True)

    do_not_contact: Mapped[bool] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(AwareDateTime)
    updated_at: Mapped[datetime] = mapped_column(AwareDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(AwareDateTime, nullable=True)
    # Next eligible retry time (deterministic retry schedule); NULL when no retry is pending.
    next_retry_at: Mapped[datetime | None] = mapped_column(AwareDateTime, nullable=True)

    escalated_at: Mapped[datetime | None] = mapped_column(AwareDateTime, nullable=True)
    escalation_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    stopped_at: Mapped[datetime | None] = mapped_column(AwareDateTime, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    # Processing lock (concurrency control). NULL means the case is not being processed; a
    # timestamp means a run has claimed it for the deterministic control loop. It is managed
    # only by the atomic claim/release (never by the domain-state mapping), so a case can be
    # processed by at most one run at a time and never has a second intervention executed.
    locked_at: Mapped[datetime | None] = mapped_column(AwareDateTime, nullable=True)

    signal: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Promise-to-pay lifecycle state (JSON value object), NULL when no promise was recorded.
    promise: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # ``metadata`` is reserved on declarative classes, so the attribute is renamed.
    case_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class EventRow(Base):
    """Persistent form of an :class:`~revguard.domain.Event` (for idempotency/history)."""

    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    source: Mapped[str] = mapped_column(String)
    occurred_at: Mapped[datetime] = mapped_column(AwareDateTime)

    customer_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    payment_id: Mapped[str | None] = mapped_column(String, nullable=True)
    order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    subscription_id: Mapped[str | None] = mapped_column(String, nullable=True)
    invoice_id: Mapped[str | None] = mapped_column(String, nullable=True)

    amount: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    currency: Mapped[str | None] = mapped_column(String, nullable=True)

    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)


class AuditRow(Base):
    """Append-only audit entry. ``seq`` provides a monotonic total order of writes."""

    __tablename__ = "audit_log"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    case_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(AwareDateTime)

    stage: Mapped[str] = mapped_column(String, index=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str | None] = mapped_column(String, nullable=True)

    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


__all__ = ["Base", "CaseRow", "EventRow", "AuditRow"]
