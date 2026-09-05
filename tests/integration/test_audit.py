"""Integration tests for the append-only audit log."""

from __future__ import annotations

from revguard.audit import AuditActor, AuditEntry, AuditLog, AuditStage
from revguard.persistence import Database


def test_records_are_written_and_read_in_order(database: Database):
    with database.session() as s:
        log = AuditLog(s)
        log.record_event(
            stage=AuditStage.CASE_CREATED, actor=AuditActor.SYSTEM, case_id="case_1"
        )
        log.record_event(
            stage=AuditStage.DIAGNOSIS,
            actor=AuditActor.AI,
            case_id="case_1",
            action="retry_payment",
            details={"confidence": 0.8},
        )
        log.record_event(
            stage=AuditStage.POLICY_DECISION,
            actor=AuditActor.POLICY,
            case_id="case_1",
            action="APPROVE",
        )

    with database.session() as s:
        entries = AuditLog(s).for_case("case_1")

    assert [e.stage for e in entries] == [
        AuditStage.CASE_CREATED,
        AuditStage.DIAGNOSIS,
        AuditStage.POLICY_DECISION,
    ]
    # seq is monotonically increasing in append order.
    seqs = [e.seq for e in entries]
    assert seqs == sorted(seqs)
    assert all(e.seq is not None for e in entries)
    assert entries[1].actor == AuditActor.AI
    assert entries[1].action == "retry_payment"
    assert entries[1].details == {"confidence": 0.8}


def test_record_returns_entry_with_seq(database: Database):
    with database.session() as s:
        stored = AuditLog(s).record(
            AuditEntry(stage=AuditStage.NOTE, actor=AuditActor.HUMAN, case_id="case_x")
        )
    assert stored.seq is not None


def test_all_returns_global_order_across_cases(database: Database):
    with database.session() as s:
        log = AuditLog(s)
        log.record_event(stage=AuditStage.NOTE, actor=AuditActor.SYSTEM, case_id="a")
        log.record_event(stage=AuditStage.NOTE, actor=AuditActor.SYSTEM, case_id="b")
        log.record_event(stage=AuditStage.NOTE, actor=AuditActor.SYSTEM, case_id="a")

    with database.session() as s:
        everything = AuditLog(s).all()

    assert [e.case_id for e in everything] == ["a", "b", "a"]


def test_audit_survives_new_session(database: Database):
    with database.session() as s:
        AuditLog(s).record_event(
            stage=AuditStage.ESCALATION, actor=AuditActor.POLICY, case_id="case_1"
        )
    with database.session() as s:
        assert len(AuditLog(s).for_case("case_1")) == 1


def test_audit_timestamps_are_timezone_aware(database: Database):
    with database.session() as s:
        AuditLog(s).record_event(
            stage=AuditStage.NOTE, actor=AuditActor.SYSTEM, case_id="case_1"
        )
    with database.session() as s:
        entry = AuditLog(s).for_case("case_1")[0]
    assert entry.recorded_at.tzinfo is not None


def test_audit_log_is_append_only_no_mutation_api(database: Database):
    """The audit API exposes no update/delete — it is append-only by construction."""
    with database.session() as s:
        log = AuditLog(s)
        for forbidden in ("update", "delete", "remove", "edit", "modify"):
            assert not hasattr(log, forbidden)


def test_correlation_id_round_trips(database: Database):
    with database.session() as s:
        AuditLog(s).record_event(
            stage=AuditStage.EXECUTION,
            actor=AuditActor.EXECUTOR,
            case_id="case_1",
            action="retry_payment",
            correlation_id="evt_42",
        )
    with database.session() as s:
        entry = AuditLog(s).for_case("case_1")[0]
    assert entry.correlation_id == "evt_42"
