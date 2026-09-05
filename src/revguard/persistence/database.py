"""Database setup: engine, session factory, and a transactional session scope.

Thin wrapper around a SQLAlchemy engine. The :meth:`Database.session` context manager
commits on success and rolls back on error, so callers get correct transaction boundaries
without touching SQLAlchemy directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from revguard.persistence.models import Base


class Database:
    """Owns the engine/session factory and creates the schema for a SQLite database."""

    def __init__(self, url: str = "sqlite:///./revguard.db") -> None:
        # ``timeout`` sets SQLite's busy_timeout: a second connection contending for the write
        # lock (e.g. a concurrent case run) waits for the first to commit and then observes the
        # committed state, rather than failing immediately — this is what makes the atomic
        # claim's compare-and-swap reliable under concurrency.
        connect_args = (
            {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
        )
        self.engine = create_engine(url, future=True, connect_args=connect_args)
        self._session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, future=True
        )

    def create_all(self) -> None:
        """Create all RevGuard tables if they do not already exist."""
        Base.metadata.create_all(self.engine)
        self._ensure_columns()

    # Nullable columns added to ``recovery_cases`` after the original schema shipped. Adding a
    # new nullable column to the model? Add it here too so a pre-existing on-disk SQLite schema
    # is self-healed on startup (ADD COLUMN is additive and non-destructive). Keep in sync with
    # revguard.persistence.models.RecoveryCaseRow.
    _BACKFILL_COLUMNS: dict[str, str] = {
        "locked_at": "VARCHAR",
        "next_retry_at": "VARCHAR",  # deterministic retry scheduling
        "promise": "JSON",  # promise-to-pay lifecycle
    }

    def _ensure_columns(self) -> None:
        """Self-healing migration: add newly-introduced nullable columns to a pre-existing
        SQLite database so an older on-disk schema keeps working without a migration tool.

        Each backfilled column is nullable with no default, so ``ALTER TABLE ADD COLUMN`` is
        safe and preserves existing rows. It is a no-op when the schema is already current
        (e.g. a freshly created database), so it never affects tests or new deployments."""
        if self.engine.dialect.name != "sqlite":
            return
        with self.engine.begin() as conn:
            existing = {
                row[1] for row in conn.exec_driver_sql("PRAGMA table_info(recovery_cases)")
            }
            for name, ddl_type in self._BACKFILL_COLUMNS.items():
                if name not in existing:
                    conn.exec_driver_sql(
                        f"ALTER TABLE recovery_cases ADD COLUMN {name} {ddl_type}"
                    )

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """A unit-of-work: commit on success, roll back on any exception."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["Database"]
