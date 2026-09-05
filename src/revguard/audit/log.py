"""Append-only audit log (Stage 8).

:class:`AuditLog` is the public API for recording and reading audit entries. It exposes
**only** append and read operations — there is deliberately no update or delete method, so
the log is append-only by construction. It maps between :class:`AuditEntry` domain records
and the :class:`AuditRow` table, keeping SQLAlchemy confined to the persistence/audit layer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from revguard.audit.models import AuditActor, AuditEntry, AuditStage
from revguard.persistence.models import AuditRow


class AuditLog:
    """Append-only recorder/reader of :class:`AuditEntry` records for one session."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def record(self, entry: AuditEntry) -> AuditEntry:
        """Append an entry. Returns the stored entry with its assigned ``seq``."""
        row = AuditRow(
            audit_id=entry.audit_id,
            case_id=entry.case_id,
            recorded_at=entry.recorded_at,
            stage=entry.stage.value,
            actor=entry.actor.value,
            action=entry.action,
            details=dict(entry.details),
            correlation_id=entry.correlation_id,
        )
        self.session.add(row)
        self.session.flush()  # assigns row.seq
        return entry.model_copy(update={"seq": row.seq})

    def record_event(
        self,
        *,
        stage: AuditStage,
        actor: AuditActor,
        case_id: str | None = None,
        action: str | None = None,
        details: dict | None = None,
        correlation_id: str | None = None,
    ) -> AuditEntry:
        """Convenience wrapper that builds and appends an :class:`AuditEntry`."""
        return self.record(
            AuditEntry(
                case_id=case_id,
                stage=stage,
                actor=actor,
                action=action,
                details=details or {},
                correlation_id=correlation_id,
            )
        )

    def for_case(self, case_id: str) -> list[AuditEntry]:
        """All entries for a case, in append order."""
        rows = self.session.scalars(
            select(AuditRow)
            .where(AuditRow.case_id == case_id)
            .order_by(AuditRow.seq)
        ).all()
        return [_row_to_entry(r) for r in rows]

    def all(self) -> list[AuditEntry]:
        """Every entry, in append order."""
        rows = self.session.scalars(select(AuditRow).order_by(AuditRow.seq)).all()
        return [_row_to_entry(r) for r in rows]


def _row_to_entry(row: AuditRow) -> AuditEntry:
    return AuditEntry(
        audit_id=row.audit_id,
        case_id=row.case_id,
        recorded_at=row.recorded_at,
        stage=row.stage,
        actor=row.actor,
        action=row.action,
        details=dict(row.details or {}),
        correlation_id=row.correlation_id,
        seq=row.seq,
    )


__all__ = ["AuditLog"]
