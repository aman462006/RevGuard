"""Deterministic offline diagnoser (AGENT_SPEC §3).

Maps case features → a sensible :class:`ActionProposal` for all four workflows, with no
randomness and no network — so the whole pipeline (and the evaluator, later) runs offline
with no API key. It is also the reference oracle for tests. Imports only domain models.
"""

from __future__ import annotations

from revguard.diagnosis.diagnoser import Diagnoser
from revguard.domain import (
    ActionProposal,
    ActionType,
    RecoveryCase,
    RiskLevel,
    WorkflowType,
)

# Actions that contact the customer; the mock respects an opt-out and escalates instead.
_CONTACT_ACTIONS = frozenset({ActionType.SEND_REMINDER, ActionType.CREATE_PAYMENT_LINK})

# Actions for which it is meaningful to state an expected recovery amount.
_RECOVERY_ACTIONS = frozenset(
    {
        ActionType.RETRY_PAYMENT,
        ActionType.CREATE_PAYMENT_LINK,
        ActionType.SEND_REMINDER,
        ActionType.RECORD_PROMISE_TO_PAY,
    }
)


class MockDiagnoser(Diagnoser):
    """Deterministic feature → proposal mapping covering workflows A–D."""

    name = "mock"

    def diagnose(self, case: RecoveryCase) -> ActionProposal:
        action = self._choose_action(case)

        # Respect do-not-contact deterministically (policy also enforces this).
        if case.do_not_contact and action in _CONTACT_ACTIONS:
            action = ActionType.RECOMMEND_ESCALATION

        concrete = action in _RECOVERY_ACTIONS
        expected_amount = case.amount_at_risk if concrete else None
        expected_currency = case.currency if concrete else None

        parameters: dict[str, str | int] = {
            "idempotency_key": f"{case.case_id}:{action.value}:{case.attempt_count}",
        }

        return ActionProposal(
            case_id=case.case_id,
            action_type=action,
            rationale=self._rationale(case, action),
            confidence=0.8 if concrete else 0.55,
            parameters=parameters,
            evidence={"diagnoser": self.name, "attempt_count": case.attempt_count},
            expected_recovery_amount=expected_amount,
            expected_recovery_currency=expected_currency,
        )

    def _choose_action(self, case: RecoveryCase) -> ActionType:
        n = case.attempt_count
        workflow = case.case_type

        if workflow is WorkflowType.PAYMENT_DEGRADATION:
            # Analytics/root-cause focused: escalate real risk, otherwise wait.
            if case.signal.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                return ActionType.RECOMMEND_ESCALATION
            return ActionType.WAIT

        if workflow is WorkflowType.FAILED_SUBSCRIPTION:
            sequence = [
                ActionType.RETRY_PAYMENT,
                ActionType.CREATE_PAYMENT_LINK,
                ActionType.SEND_REMINDER,
            ]
            return sequence[n] if n < len(sequence) else ActionType.RECOMMEND_ESCALATION

        if workflow is WorkflowType.CHECKOUT_ABANDONMENT:
            sequence = [ActionType.CREATE_PAYMENT_LINK, ActionType.SEND_REMINDER]
            return sequence[n] if n < len(sequence) else ActionType.RECOMMEND_STOP

        if workflow is WorkflowType.OVERDUE_RECEIVABLE:
            sequence = [ActionType.SEND_REMINDER, ActionType.RECORD_PROMISE_TO_PAY]
            return sequence[n] if n < len(sequence) else ActionType.RECOMMEND_ESCALATION

        # Unknown workflow → fail safe toward human review.
        return ActionType.RECOMMEND_ESCALATION

    @staticmethod
    def _rationale(case: RecoveryCase, action: ActionType) -> str:
        return (
            f"[mock] workflow={case.case_type.value} attempt={case.attempt_count} "
            f"risk={case.signal.risk_level.value}: recommending {action.value}."
        )


__all__ = ["MockDiagnoser"]
