"""Deterministic MockAdapter for the hackathon (Phase 6).

Simulates payment retry, payment-link creation, reminders, promise-to-pay recording, and
wait — with **no randomness and no network**. Given the same case + proposal, it always
returns the same :class:`AdapterResult` (references are derived from a stable digest), so
the whole pipeline runs and tests reproducibly offline.

It never pretends a simulated payment is real recovered money: every result is
``simulated=True`` and reports only a *technical* outcome. Whether money was actually
recovered is decided later by verification (Phase 7).

``fail_actions`` lets tests force a deterministic technical failure for specific action
types; by default every simulated action succeeds technically.
"""

from __future__ import annotations

import hashlib

from revguard.domain import ActionProposal, ActionType, RecoveryCase
from revguard.execution.adapter import ActionAdapter, AdapterResult


def _digest(case: RecoveryCase, proposal: ActionProposal) -> str:
    """Stable 12-hex-char digest for a (case, action, attempt) — deterministic reference."""
    seed = f"{case.case_id}:{proposal.action_type.value}:{case.attempt_count}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


class MockAdapter(ActionAdapter):
    """Deterministic, offline adapter. Produces only simulated technical outcomes."""

    name = "mock"

    def __init__(self, *, fail_actions: frozenset[ActionType] | None = None) -> None:
        self._fail_actions = fail_actions or frozenset()
        # Records (action, case_id) for each performed call — lets tests assert the adapter
        # ran exactly once (idempotency) or not at all (rejection).
        self.calls: list[tuple[ActionType, str]] = []

    def _outcome(
        self,
        case: RecoveryCase,
        proposal: ActionProposal,
        *,
        ref_prefix: str | None,
        detail: str,
        url: str | None = None,
    ) -> AdapterResult:
        action = proposal.action_type
        self.calls.append((action, case.case_id))

        if action in self._fail_actions:
            return AdapterResult(
                action=action,
                accepted=True,
                succeeded=False,
                detail=f"simulated {action.value} did not complete",
                failure_reason=f"simulated failure for {action.value}",
                simulated=True,
            )

        reference = None if ref_prefix is None else f"{ref_prefix}_{_digest(case, proposal)}"
        return AdapterResult(
            action=action,
            accepted=True,
            succeeded=True,
            detail=detail,
            reference=reference,
            url=url,
            simulated=True,
        )

    def retry_payment(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult:
        return self._outcome(
            case, proposal, ref_prefix="sim_pay", detail="simulated payment retry submitted"
        )

    def create_payment_link(
        self, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult:
        # A deliberately non-Razorpay, obviously-demo URL so a simulated link is never mistaken
        # for a real Razorpay short_url (rzp.io/...). Real links come only from RazorpayTestAdapter.
        return self._outcome(
            case,
            proposal,
            ref_prefix="sim_link",
            detail="simulated payment link created",
            url=f"https://demo.revguard.local/pay/{_digest(case, proposal)}",
        )

    def send_reminder(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult:
        return self._outcome(
            case, proposal, ref_prefix="sim_msg", detail="simulated reminder dispatched"
        )

    def record_promise_to_pay(
        self, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult:
        return self._outcome(
            case, proposal, ref_prefix="sim_ptp", detail="simulated promise-to-pay recorded"
        )

    def wait(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult:
        # WAIT causes no external side effect and produces no provider reference.
        return self._outcome(case, proposal, ref_prefix=None, detail="waiting for next step")


__all__ = ["MockAdapter"]
