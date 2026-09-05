"""RevGuard persistence layer (Phase 3) — SQLite + SQLAlchemy.

SQLAlchemy is confined to this package (and ``audit``); domain models stay pure Pydantic.
Exposes the :class:`Database` (engine + transactional session scope), the ORM tables, and
repositories that map domain aggregates to/from rows.
"""

from revguard.persistence.database import Database
from revguard.persistence.models import AuditRow, Base, CaseRow, EventRow
from revguard.persistence.repositories import (
    CaseRepository,
    EventRepository,
    RecordAlreadyExists,
    reset_all,
)

__all__ = [
    "Database",
    "Base",
    "CaseRow",
    "EventRow",
    "AuditRow",
    "CaseRepository",
    "EventRepository",
    "RecordAlreadyExists",
    "reset_all",
]
