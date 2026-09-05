"""Recovery lifecycle: the honest "customer did not pay" path and the demo success double.

These exercise the real orchestrator + PolicyEngine (never a second retry system):

    intervention → WAITING → not paid → NOT_RECOVERED → bounded retry
    → … → retries exhausted → STOP/ESCALATE → ₹0 recovered

and the demo-only success double that drives a WAITING case to a *verified* RECOVERED. The
agent is wired exactly like demo mode (MockDiagnoser/MockAdapter/MockVerifier PENDING), so a
run parks in WAITING and recovery is never fabricated on link/order creation alone.
"""

from __future__ import annotations

from decimal import Decimal

from revguard.audit import AuditLog, AuditStage
from revguard.diagnosis import MockDiagnoser
from revguard.domain import CaseStatus, VerificationStatus, WorkflowType
from revguard.execution import MockAdapter
from revguard.orchestrator import RecoveryAgent
from revguard.persistence import CaseRepository, Database
from revguard.verification import MockVerifier


def _demo_agent(db: Database) -> RecoveryAgent:
    # Mirrors dependencies.get_run_agent in demo mode: creating a link/order is only PENDING.
    return RecoveryAgent(
        db,
        diagnoser=MockDiagnoser(),
        adapter=MockAdapter(),
        verifier=MockVerifier(default=VerificationStatus.PENDING),
    )


def _load(db: Database, case_id: str):
    with db.session() as s:
        return CaseRepository(s).get(case_id)


def _audit(db: Database, case_id: str):
    with db.session() as s:
        return AuditLog(s).for_case(case_id)


def test_run_parks_in_waiting_and_is_not_recovered(database, make_case):
    # RECOVERED only after verification: executing an action is not recovery.
    agent = _demo_agent(database)
    case = make_case()
    agent.process_case(case)

    fresh = _load(database, case.case_id)
    assert fresh.status is CaseStatus.WAITING
    assert fresh.amount_recovered == Decimal("0")
    assert fresh.attempt_count == 1  # one intervention executed, pending payment


def test_verify_unpaid_records_not_recovered_and_retries(database, make_case):
    # WAITING → NOT_RECOVERED → another bounded attempt (still ₹0).
    agent = _demo_agent(database)
    case = make_case()
    agent.process_case(case)

    agent.verify_unpaid(_load(database, case.case_id))

    fresh = _load(database, case.case_id)
    assert fresh.amount_recovered == Decimal("0")
    assert fresh.attempt_count == 2, "a second bounded attempt should have run"

    # The non-payment is recorded as a verified NOT_RECOVERED in the audit trail.
    verifications = [
        e
        for e in _audit(database, case.case_id)
        if e.stage is AuditStage.VERIFICATION
        and e.details.get("verification_status") == VerificationStatus.NOT_RECOVERED.value
    ]
    assert verifications, "expected a NOT_RECOVERED verification entry"
    assert verifications[0].details.get("amount_recovered") == "0"


def test_unpaid_retries_exhaust_to_escalate_with_zero(database, make_case):
    # Failed subscription: retry → payment link → reminder → (sequence exhausted) → ESCALATE.
    agent = _demo_agent(database)
    case = make_case(case_type=WorkflowType.FAILED_SUBSCRIPTION)
    agent.process_case(case)

    for _ in range(3):
        agent.verify_unpaid(_load(database, case.case_id))

    fresh = _load(database, case.case_id)
    assert fresh.status is CaseStatus.ESCALATED
    assert fresh.amount_recovered == Decimal("0")
    # Further "not paid" clicks on a terminal case are a harmless no-op (still ₹0).
    agent.verify_unpaid(_load(database, case.case_id))
    assert _load(database, case.case_id).amount_recovered == Decimal("0")


def test_unpaid_retries_exhaust_to_stop_with_zero(database, make_case, make_signal):
    # Checkout abandonment: payment link → reminder → (sequence exhausted) → STOP.
    agent = _demo_agent(database)
    case = make_case(
        case_type=WorkflowType.CHECKOUT_ABANDONMENT,
        signal=make_signal(
            signal_type=WorkflowType.CHECKOUT_ABANDONMENT,
            subscription_id=None,
            order_id="ord_1",
        ),
        amount_at_risk=Decimal("6000.00"),
    )
    agent.process_case(case)

    for _ in range(2):
        agent.verify_unpaid(_load(database, case.case_id))

    fresh = _load(database, case.case_id)
    assert fresh.status is CaseStatus.STOPPED
    assert fresh.amount_recovered == Decimal("0")


def test_demo_simulate_payment_confirms_verified_recovery(database, make_case):
    # The demo success double drives WAITING → RECOVERED through the verified reconciliation
    # path — the recovered amount comes from verification, capped at the amount at risk.
    agent = _demo_agent(database)
    case = make_case()
    agent.process_case(case)
    assert _load(database, case.case_id).status is CaseStatus.WAITING

    recon = agent.simulate_test_recovery(_load(database, case.case_id))
    assert recon.reconciled is True

    fresh = _load(database, case.case_id)
    assert fresh.status is CaseStatus.RECOVERED
    assert fresh.amount_recovered == case.amount_at_risk


def test_recheck_payment_recovers_on_verified_paid_status(database, make_case):
    # Production re-poll: the verifier reports the payment paid on the second check → RECOVERED
    # with the verified amount. Nothing is fabricated; recovery comes from the verifier.
    agent = RecoveryAgent(
        database,
        diagnoser=MockDiagnoser(),
        adapter=MockAdapter(),
        verifier=MockVerifier(
            sequence=[VerificationStatus.PENDING, VerificationStatus.RECOVERED]
        ),
    )
    case = make_case()
    agent.process_case(case)  # first verify → PENDING → WAITING
    assert _load(database, case.case_id).status is CaseStatus.WAITING

    agent.recheck_payment(_load(database, case.case_id))  # second verify → RECOVERED

    fresh = _load(database, case.case_id)
    assert fresh.status is CaseStatus.RECOVERED
    assert fresh.amount_recovered == case.amount_at_risk


def test_recheck_payment_stays_waiting_when_still_unpaid(database, make_case):
    # A re-poll that finds no verified payment must not fabricate recovery — it stays WAITING.
    agent = _demo_agent(database)  # verifier always PENDING
    case = make_case()
    agent.process_case(case)

    agent.recheck_payment(_load(database, case.case_id))

    fresh = _load(database, case.case_id)
    assert fresh.status is CaseStatus.WAITING
    assert fresh.amount_recovered == Decimal("0")
