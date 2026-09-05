"""Repositories mapping between domain models and ORM rows.

Each repository operates on a caller-provided :class:`Session` (the caller owns the
transaction, typically via :meth:`Database.session`). Methods are deliberately small and
map explicitly, so the domain layer never imports SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from revguard.domain import Event, PromiseToPay, RecoveryCase, RevenueRiskSignal
from revguard.persistence.models import AuditRow, CaseRow, EventRow


class RecordAlreadyExists(Exception):
    """Raised when a strict ``add`` would overwrite an existing primary key."""


def reset_all(session: Session) -> dict[str, int]:
    """Delete every case, event, and audit entry — a full "start from scratch" reset.

    A deliberate maintenance operation (used by the dashboard's *Reset* control) so a reviewer
    can clear the demo board and re-run from zero. It is kept separate from the append-only
    :class:`~revguard.audit.log.AuditLog`, which never deletes during normal operation. Events
    are cleared too: detection re-scans all stored events, so leaving them would resurrect the
    same cases on the next run. Returns the number of rows removed per table.
    """
    counts = {
        "cases": int(session.scalar(select(func.count()).select_from(CaseRow)) or 0),
        "events": int(session.scalar(select(func.count()).select_from(EventRow)) or 0),
        "audit": int(session.scalar(select(func.count()).select_from(AuditRow)) or 0),
    }
    session.execute(delete(AuditRow))
    session.execute(delete(EventRow))
    session.execute(delete(CaseRow))
    session.flush()
    return counts


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class CaseRepository:
    """Persist and load :class:`RecoveryCase` aggregates."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, case: RecoveryCase) -> None:
        """Insert a new case. Raises if the ``case_id`` already exists."""
        if self.session.get(CaseRow, case.case_id) is not None:
            raise RecordAlreadyExists(f"case {case.case_id!r} already exists")
        self.session.add(_case_to_row(case))
        self.session.flush()

    def add_if_absent(self, case: RecoveryCase) -> bool:
        """Insert a case unless one with the same ``case_id`` already exists.

        Returns ``True`` if this call inserted the row, ``False`` if it was already present.
        Safe against a concurrent insert of the same case: a losing race that trips the primary
        key is rolled back and reported as "already present" (``False``), never raised.
        """
        if self.session.get(CaseRow, case.case_id) is not None:
            return False
        self.session.add(_case_to_row(case))
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            return False
        return True

    def try_claim(
        self,
        case_id: str,
        *,
        now: datetime,
        stale_before: datetime,
        terminal_statuses: Sequence[str],
    ) -> bool:
        """Atomically acquire the processing lock for a case (compare-and-swap).

        Sets ``locked_at = now`` **iff** the case exists, is not in a terminal status, and is
        either unlocked or holds a lock older than ``stale_before`` (an abandoned lock from a
        crashed run). Returns ``True`` only for the single caller that acquires it; a concurrent
        caller gets ``False`` and must not execute another intervention. Excluding terminal
        statuses means a stale caller can never re-process a case another run already completed.

        This UPDATE is the only statement in the caller's transaction, so it evaluates against
        the latest committed state and settles the race deterministically.
        """
        result = self.session.execute(
            update(CaseRow)
            .where(
                CaseRow.case_id == case_id,
                CaseRow.status.not_in(list(terminal_statuses)),
                or_(CaseRow.locked_at.is_(None), CaseRow.locked_at < stale_before),
            )
            .values(locked_at=now)
        )
        self.session.flush()
        return bool(result.rowcount == 1)

    def release(self, case_id: str) -> None:
        """Release the processing lock (idempotent). Always safe to call in a ``finally``."""
        self.session.execute(
            update(CaseRow).where(CaseRow.case_id == case_id).values(locked_at=None)
        )
        self.session.flush()

    def is_locked(self, case_id: str) -> bool:
        """Whether the case currently holds a processing lock (inspection/tests)."""
        row = self.session.get(CaseRow, case_id)
        return row is not None and row.locked_at is not None

    def save(self, case: RecoveryCase) -> None:
        """Insert or update a case (idempotent upsert on ``case_id``)."""
        row = self.session.get(CaseRow, case.case_id)
        if row is None:
            self.session.add(_case_to_row(case))
        else:
            _apply_case_to_row(case, row)
        self.session.flush()

    def get(self, case_id: str) -> RecoveryCase | None:
        row = self.session.get(CaseRow, case_id)
        return _row_to_case(row) if row is not None else None

    def list_all(self) -> list[RecoveryCase]:
        rows = self.session.scalars(select(CaseRow).order_by(CaseRow.created_at)).all()
        return [_row_to_case(r) for r in rows]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EventRepository:
    """Persist :class:`Event` inputs, with idempotent (duplicate-safe) inserts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_if_absent(self, event: Event) -> bool:
        """Insert an event unless one with the same ``event_id`` exists.

        Returns ``True`` if inserted, ``False`` if it was already present. Safe to call
        repeatedly for the same event (idempotency).
        """
        if self.session.get(EventRow, event.event_id) is not None:
            return False
        self.session.add(_event_to_row(event))
        self.session.flush()
        return True

    def get(self, event_id: str) -> Event | None:
        row = self.session.get(EventRow, event_id)
        return _row_to_event(row) if row is not None else None

    def list_all(self) -> list[Event]:
        rows = self.session.scalars(
            select(EventRow).order_by(EventRow.occurred_at)
        ).all()
        return [_row_to_event(r) for r in rows]


# ---------------------------------------------------------------------------
# Mapping helpers (domain <-> row)
# ---------------------------------------------------------------------------


def _case_to_row(case: RecoveryCase) -> CaseRow:
    row = CaseRow(case_id=case.case_id)
    _apply_case_to_row(case, row)
    return row


def _apply_case_to_row(case: RecoveryCase, row: CaseRow) -> None:
    row.case_type = case.case_type.value
    row.customer_id = case.customer_id
    row.amount_at_risk = case.amount_at_risk
    row.currency = case.currency.value
    row.amount_recovered = case.amount_recovered
    row.status = case.status.value
    row.attempt_count = case.attempt_count
    row.current_step = case.current_step
    row.current_action = case.current_action.value if case.current_action else None
    row.next_action = case.next_action.value if case.next_action else None
    row.do_not_contact = case.do_not_contact
    row.created_at = case.created_at
    row.updated_at = case.updated_at
    row.expires_at = case.expires_at
    row.next_retry_at = case.next_retry_at
    row.escalated_at = case.escalated_at
    row.escalation_reason = case.escalation_reason
    row.stopped_at = case.stopped_at
    row.stop_reason = case.stop_reason.value if case.stop_reason else None
    row.signal = case.signal.model_dump(mode="json")
    row.promise = case.promise.model_dump(mode="json") if case.promise else None
    row.case_metadata = dict(case.metadata)


def _row_to_case(row: CaseRow) -> RecoveryCase:
    return RecoveryCase(
        case_id=row.case_id,
        case_type=row.case_type,
        customer_id=row.customer_id,
        signal=RevenueRiskSignal.model_validate(row.signal),
        amount_at_risk=row.amount_at_risk,
        currency=row.currency,
        amount_recovered=row.amount_recovered,
        status=row.status,
        attempt_count=row.attempt_count,
        current_step=row.current_step,
        current_action=row.current_action,
        next_action=row.next_action,
        do_not_contact=row.do_not_contact,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        next_retry_at=row.next_retry_at,
        escalated_at=row.escalated_at,
        escalation_reason=row.escalation_reason,
        stopped_at=row.stopped_at,
        stop_reason=row.stop_reason,
        promise=PromiseToPay.model_validate(row.promise) if row.promise else None,
        metadata=dict(row.case_metadata or {}),
    )


def _event_to_row(event: Event) -> EventRow:
    return EventRow(
        event_id=event.event_id,
        event_type=event.event_type.value,
        source=event.source.value,
        occurred_at=event.occurred_at,
        customer_id=event.customer_id,
        payment_id=event.payment_id,
        order_id=event.order_id,
        subscription_id=event.subscription_id,
        invoice_id=event.invoice_id,
        amount=event.amount,
        currency=event.currency.value if event.currency else None,
        event_metadata=dict(event.metadata),
    )


def _row_to_event(row: EventRow) -> Event:
    return Event(
        event_id=row.event_id,
        event_type=row.event_type,
        source=row.source,
        occurred_at=row.occurred_at,
        customer_id=row.customer_id,
        payment_id=row.payment_id,
        order_id=row.order_id,
        subscription_id=row.subscription_id,
        invoice_id=row.invoice_id,
        amount=row.amount,
        currency=row.currency,
        metadata=dict(row.event_metadata or {}),
    )


__all__ = [
    "CaseRepository",
    "EventRepository",
    "RecordAlreadyExists",
    "reset_all",
]
