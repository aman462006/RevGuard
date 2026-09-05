"""Webhook -> RecoveryCase reconciliation (Phase 9 follow-up).

A verified, out-of-band collection (modelled as a :class:`RecoveryConfirmation`, e.g. from a
signature-checked Razorpay *paid* webhook) is reconciled to the case that initiated it. These
tests drive ``RecoveryAgent.reconcile_recovery`` directly against a temporary SQLite database
(the ``database`` / ``make_case`` fixtures come from conftest.py). No network, no real payment.

Invariants under test: recovery is marked only from a verified confirmation; only a case that is
awaiting verification (WAITING/ACTION_EXECUTING) is confirmed (the state machine is not
bypassed); matching works by explicit case reference and by entity id; and it is idempotent.
"""

from __future__ import annotations

from decimal import Decimal

from revguard.audit import AuditLog
from revguard.domain import CaseStatus, Currency
from revguard.orchestrator import RecoveryAgent, RecoveryConfirmation
from revguard.persistence import CaseRepository, Database


def _seed(db: Database, case) -> None:
    with db.session() as s:
        CaseRepository(s).add(case)


def _load(db: Database, case_id: str):
    with db.session() as s:
        return CaseRepository(s).get(case_id)


def _stages(db: Database, case_id: str) -> list[str]:
    with db.session() as s:
        return [e.stage.value for e in AuditLog(s).for_case(case_id)]


def test_reconcile_confirms_waiting_case_by_reference(database, make_case):
    case = make_case(status=CaseStatus.WAITING)
    _seed(database, case)
    agent = RecoveryAgent(database)

    result = agent.reconcile_recovery(
        RecoveryConfirmation(
            reference="pay_abc",
            amount=Decimal("1500.00"),
            currency=Currency.INR,
            case_reference=case.case_id,
        )
    )

    assert result.matched and result.reconciled
    assert result.amount_recovered == Decimal("1500.00")
    stored = _load(database, case.case_id)
    assert stored.status is CaseStatus.RECOVERED
    assert stored.amount_recovered == Decimal("1500.00")
    stages = _stages(database, case.case_id)
    assert "verification" in stages and "recovery_result" in stages


def test_reconcile_matches_by_entity_id_without_reference(database, make_case, make_signal):
    # No explicit case reference — match on the subscription id carried by the signal.
    case = make_case(status=CaseStatus.WAITING, signal=make_signal(subscription_id="sub_xyz"))
    _seed(database, case)
    agent = RecoveryAgent(database)

    result = agent.reconcile_recovery(
        RecoveryConfirmation(reference="pay_1", subscription_id="sub_xyz")
    )

    assert result.matched and result.reconciled
    assert _load(database, case.case_id).status is CaseStatus.RECOVERED


def test_reconcile_partial_amount_marks_partial_recovery(database, make_case):
    case = make_case(status=CaseStatus.WAITING, amount_at_risk=Decimal("1500.00"))
    _seed(database, case)
    agent = RecoveryAgent(database)

    result = agent.reconcile_recovery(
        RecoveryConfirmation(
            reference="pay_1", amount=Decimal("600.00"), currency=Currency.INR,
            case_reference=case.case_id,
        )
    )

    assert result.reconciled
    assert _load(database, case.case_id).amount_recovered == Decimal("600.00")


def test_reconcile_caps_amount_at_risk(database, make_case):
    case = make_case(status=CaseStatus.WAITING, amount_at_risk=Decimal("1500.00"))
    _seed(database, case)
    agent = RecoveryAgent(database)

    agent.reconcile_recovery(
        RecoveryConfirmation(
            reference="pay_1", amount=Decimal("9999.00"), currency=Currency.INR,
            case_reference=case.case_id,
        )
    )
    assert _load(database, case.case_id).amount_recovered == Decimal("1500.00")


def test_reconcile_is_idempotent(database, make_case):
    case = make_case(status=CaseStatus.WAITING)
    _seed(database, case)
    agent = RecoveryAgent(database)
    conf = RecoveryConfirmation(
        reference="pay_1", amount=Decimal("1500.00"), currency=Currency.INR,
        case_reference=case.case_id,
    )

    first = agent.reconcile_recovery(conf)
    second = agent.reconcile_recovery(conf)

    assert first.reconciled is True
    assert second.matched is True and second.reconciled is False
    assert "already recovered" in second.reason
    assert _load(database, case.case_id).amount_recovered == Decimal("1500.00")


def test_reconcile_no_match_returns_unmatched(database, make_case):
    _seed(database, make_case(status=CaseStatus.WAITING))
    agent = RecoveryAgent(database)

    result = agent.reconcile_recovery(
        RecoveryConfirmation(reference="pay_1", case_reference="case_does_not_exist")
    )
    assert result.matched is False and result.reconciled is False


def test_reconcile_does_not_force_non_waiting_case(database, make_case):
    # A case still in DETECTED must NOT be jumped straight to RECOVERED (state machine intact).
    case = make_case(status=CaseStatus.DETECTED)
    _seed(database, case)
    agent = RecoveryAgent(database)

    result = agent.reconcile_recovery(
        RecoveryConfirmation(
            reference="pay_1", amount=Decimal("1500.00"), currency=Currency.INR,
            case_reference=case.case_id,
        )
    )
    assert result.matched is True and result.reconciled is False
    assert "not awaiting verification" in result.reason
    assert _load(database, case.case_id).status is CaseStatus.DETECTED
