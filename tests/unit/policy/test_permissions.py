"""Per-workflow permission whitelist (POLICY_SPEC §4)."""

from __future__ import annotations

from revguard.domain import ActionType, WorkflowType
from revguard.policy import WORKFLOW_PERMISSIONS, is_action_permitted, permitted_actions


def test_every_workflow_has_an_explicit_whitelist():
    assert set(WORKFLOW_PERMISSIONS) == set(WorkflowType)


def test_wait_is_permitted_in_every_workflow():
    for workflow in WorkflowType:
        assert is_action_permitted(workflow, ActionType.WAIT)


def test_payment_degradation_permits_no_recovery_action():
    wf = WorkflowType.PAYMENT_DEGRADATION
    assert permitted_actions(wf) == frozenset({ActionType.WAIT})
    assert not is_action_permitted(wf, ActionType.RETRY_PAYMENT)


def test_failed_subscription_permits_retry_link_reminder():
    wf = WorkflowType.FAILED_SUBSCRIPTION
    assert is_action_permitted(wf, ActionType.RETRY_PAYMENT)
    assert is_action_permitted(wf, ActionType.CREATE_PAYMENT_LINK)
    assert is_action_permitted(wf, ActionType.SEND_REMINDER)
    assert not is_action_permitted(wf, ActionType.RECORD_PROMISE_TO_PAY)


def test_checkout_abandonment_permits_contact_and_link_only():
    wf = WorkflowType.CHECKOUT_ABANDONMENT
    assert is_action_permitted(wf, ActionType.CREATE_PAYMENT_LINK)
    assert is_action_permitted(wf, ActionType.SEND_REMINDER)
    assert not is_action_permitted(wf, ActionType.RETRY_PAYMENT)


def test_overdue_receivable_permits_reminder_and_promise():
    wf = WorkflowType.OVERDUE_RECEIVABLE
    assert is_action_permitted(wf, ActionType.SEND_REMINDER)
    assert is_action_permitted(wf, ActionType.RECORD_PROMISE_TO_PAY)
    assert not is_action_permitted(wf, ActionType.CREATE_PAYMENT_LINK)


def test_recommendation_actions_are_never_whitelisted():
    for workflow in WorkflowType:
        for action in (
            ActionType.RECOMMEND_ESCALATION,
            ActionType.RECOMMEND_STOP,
            ActionType.NO_ACTION,
        ):
            assert not is_action_permitted(workflow, action)
