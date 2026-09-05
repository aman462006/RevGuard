"""ActionProposal serialization/deserialization round-trips."""

from __future__ import annotations

from revguard.diagnosis import MockDiagnoser
from revguard.domain import ActionProposal, WorkflowType


def test_proposal_json_round_trip(make_case):
    proposal = MockDiagnoser().diagnose(make_case(WorkflowType.FAILED_SUBSCRIPTION))
    raw = proposal.model_dump_json()
    restored = ActionProposal.model_validate_json(raw)
    assert restored == proposal


def test_proposal_dict_round_trip(make_case):
    proposal = MockDiagnoser().diagnose(make_case(WorkflowType.OVERDUE_RECEIVABLE))
    restored = ActionProposal.model_validate(proposal.model_dump(mode="json"))
    assert restored.action_type == proposal.action_type
    assert restored.case_id == proposal.case_id
    assert restored.expected_recovery_amount == proposal.expected_recovery_amount
