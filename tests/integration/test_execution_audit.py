"""The executor records execution through the real AuditLog abstraction (no DB coupling).

Proves the :class:`AuditSink` protocol lets the executor persist audit entries via the
existing append-only ``AuditLog`` without the executor importing any persistence type.
"""

from __future__ import annotations

from revguard.audit import AuditActor, AuditLog, AuditStage
from revguard.domain import ActionProposal, ActionType, DecisionType, PolicyDecision
from revguard.execution import ActionExecutor, MockAdapter
from revguard.persistence import Database


def test_executor_writes_execution_audit_entry(database: Database, make_case):
    case = make_case()
    proposal = ActionProposal(
        case_id=case.case_id,
        action_type=ActionType.RETRY_PAYMENT,
        rationale="retry the failed mandate",
        confidence=0.8,
    )
    decision = PolicyDecision(
        case_id=case.case_id,
        decision=DecisionType.APPROVE,
        proposed_action=ActionType.RETRY_PAYMENT,
        reason="permitted",
        matched_rules=["approve.permitted_action"],
    )

    with database.session() as s:
        executor = ActionExecutor(MockAdapter(), audit=AuditLog(s))
        executor.execute(decision, proposal, case, correlation_id="evt_1")

    with database.session() as s:
        entries = AuditLog(s).for_case(case.case_id)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.stage is AuditStage.EXECUTION
    assert entry.actor is AuditActor.EXECUTOR
    assert entry.action == ActionType.RETRY_PAYMENT.value
    assert entry.correlation_id == "evt_1"
    assert entry.details["simulated"] is True
