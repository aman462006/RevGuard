"""Integration tests for the SQLite persistence layer (cases + events)."""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest

from revguard.domain import (
    CaseStatus,
    Currency,
    Event,
    EventSource,
    EventType,
)
from revguard.persistence import (
    CaseRepository,
    Database,
    EventRepository,
    RecordAlreadyExists,
)


def test_create_all_backfills_missing_nullable_columns(tmp_path, make_case):
    # A pre-existing SQLite DB from an older schema (no next_retry_at / promise) must be
    # self-healed on startup so the current code can read/write it — this is the exact drift
    # that made an old on-disk database 500 on every case query.
    import sqlite3

    path = tmp_path / "stale.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE recovery_cases (case_id VARCHAR PRIMARY KEY, status VARCHAR)")
    con.commit()
    con.close()

    db = Database(f"sqlite:///{path}")
    db.create_all()  # must backfill the missing columns without error
    with db.engine.begin() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(recovery_cases)")}
    db.dispose()

    assert {"next_retry_at", "promise", "locked_at"} <= cols


def test_create_and_read_case(database: Database, make_case):
    case = make_case()
    with database.session() as s:
        CaseRepository(s).add(case)

    with database.session() as s:
        loaded = CaseRepository(s).get(case.case_id)

    assert loaded is not None
    assert loaded.case_id == case.case_id
    assert loaded.case_type == case.case_type
    assert loaded.status == CaseStatus.DETECTED
    assert loaded.signal.signal_id == case.signal.signal_id
    assert loaded.metadata == {"origin": "synthetic"}


def test_update_case(database: Database, make_case):
    case = make_case()
    with database.session() as s:
        CaseRepository(s).add(case)

    # Mutate the domain object and persist the change.
    with database.session() as s:
        repo = CaseRepository(s)
        loaded = repo.get(case.case_id)
        loaded.status = CaseStatus.ANALYZING
        loaded.attempt_count = 2
        loaded.amount_recovered = Decimal("500.00")
        repo.save(loaded)

    with database.session() as s:
        reloaded = CaseRepository(s).get(case.case_id)

    assert reloaded.status == CaseStatus.ANALYZING
    assert reloaded.attempt_count == 2
    assert reloaded.amount_recovered == Decimal("500.00")


def test_persistence_survives_new_database_instance(db_url: str, make_case):
    case = make_case()
    db1 = Database(db_url)
    db1.create_all()
    with db1.session() as s:
        CaseRepository(s).add(case)
    db1.dispose()

    # A brand-new Database pointing at the same file must see the data.
    db2 = Database(db_url)
    with db2.session() as s:
        loaded = CaseRepository(s).get(case.case_id)
    db2.dispose()

    assert loaded is not None
    assert loaded.case_id == case.case_id


def test_duplicate_case_add_is_rejected(database: Database, make_case):
    case = make_case()
    with database.session() as s:
        CaseRepository(s).add(case)
    with database.session() as s:
        with pytest.raises(RecordAlreadyExists):
            CaseRepository(s).add(case)

    # Only one row persisted.
    with database.session() as s:
        assert len(CaseRepository(s).list_all()) == 1


def test_decimal_amounts_round_trip_exactly(database: Database, make_case):
    tricky = Decimal("1234567.89")
    case = make_case(amount_at_risk=tricky)
    with database.session() as s:
        CaseRepository(s).add(case)

    with database.session() as s:
        loaded = CaseRepository(s).get(case.case_id)

    assert loaded.amount_at_risk == tricky
    assert isinstance(loaded.amount_at_risk, Decimal)
    assert str(loaded.amount_at_risk) == "1234567.89"  # scale preserved, no float drift


def test_timezone_aware_timestamps_round_trip(database: Database, make_case):
    case = make_case()
    with database.session() as s:
        CaseRepository(s).add(case)

    with database.session() as s:
        loaded = CaseRepository(s).get(case.case_id)

    assert loaded.created_at.tzinfo is not None
    assert loaded.created_at == case.created_at  # same instant, tz-aware
    # Equal to the UTC instant.
    assert loaded.created_at == case.created_at.astimezone(UTC)


def _event(**overrides) -> Event:
    defaults = dict(
        event_id="evt_dup_1",
        event_type=EventType.SUBSCRIPTION_PAYMENT_FAILED,
        source=EventSource.SYNTHETIC,
        customer_id="cust_1",
        subscription_id="sub_1",
        amount=Decimal("1500.00"),
        currency=Currency.INR,
        metadata={"failure_reason": "insufficient_funds"},
    )
    defaults.update(overrides)
    return Event(**defaults)


def test_duplicate_event_ids_handled_idempotently(database: Database):
    event = _event()
    with database.session() as s:
        repo = EventRepository(s)
        assert repo.add_if_absent(event) is True
        # Same id again within the same transaction -> no-op.
        assert repo.add_if_absent(event) is False

    # And across a new session.
    with database.session() as s:
        repo = EventRepository(s)
        assert repo.add_if_absent(event) is False
        assert len(repo.list_all()) == 1


def test_event_round_trips_amount_and_metadata(database: Database):
    event = _event(amount=Decimal("99.95"))
    with database.session() as s:
        EventRepository(s).add_if_absent(event)

    with database.session() as s:
        loaded = EventRepository(s).get(event.event_id)

    assert loaded is not None
    assert loaded.amount == Decimal("99.95")
    assert loaded.currency == Currency.INR
    assert loaded.metadata == {"failure_reason": "insufficient_funds"}
    assert loaded.occurred_at.tzinfo is not None


def test_rollback_on_error_leaves_no_partial_write(database: Database, make_case):
    case = make_case()
    with pytest.raises(RuntimeError):
        with database.session() as s:
            CaseRepository(s).add(case)
            raise RuntimeError("boom after write")

    with database.session() as s:
        assert CaseRepository(s).get(case.case_id) is None
