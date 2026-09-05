"""RevGuard audit layer (Stage 8) — an append-only log of workflow transitions.

:class:`AuditLog` records/reads immutable :class:`AuditEntry` records. There is no update
or delete API: the log is append-only by construction, forming the source of truth for
metrics and evaluation reconstruction in later phases.
"""

from revguard.audit.log import AuditLog
from revguard.audit.models import AuditActor, AuditEntry, AuditStage

__all__ = ["AuditLog", "AuditEntry", "AuditActor", "AuditStage"]
