"""The deterministic PolicyEngine — the single approval gate (POLICY_SPEC §1, §2).

``evaluate(proposal, case, context)`` returns exactly one :class:`PolicyDecision`
(APPROVE / ESCALATE / STOP). It is a **pure function** over the inputs:

* it never executes an action, calls an LLM, touches the database, or performs any I/O;
* AI confidence can never override a policy violation — an ``ActionProposal`` is only ever
  a recommendation, and a violated rule always wins over a permitted-looking action;
* it fails closed — if a required fact is missing or a rule cannot safely decide, the
  outcome is ESCALATE or STOP, never APPROVE.

Rules are evaluated in a fixed precedence; the first that fires wins, so decisions are
deterministic and every one carries the firing rule's id + reason for the audit log.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from revguard.domain import (
    ActionProposal,
    DecisionType,
    PolicyDecision,
    RecoveryCase,
)
from revguard.policy import guardrails, retry, stopping_rules
from revguard.policy.context import (
    PolicyConfig,
    PolicyContext,
    RuleId,
    RuleOutcome,
)
from revguard.policy.permissions import check_permission

# A rule takes the four inputs and either fires (RuleOutcome) or passes (None).
Rule = Callable[[ActionProposal, RecoveryCase, PolicyContext, PolicyConfig], "RuleOutcome | None"]


class PolicyEngine:
    """Combines all policy checks deterministically into a single decision."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig()
        # Fixed precedence — first firing rule wins. Ordered fail-closed:
        #   preconditions → terminal stops → AI intent → permission → thresholds → cooldown.
        self._rules: tuple[Rule, ...] = (
            # Fail-closed precondition.
            guardrails.check_case_consistency,
            # Hard, terminal stops (win over everything, incl. AI recommendations).
            stopping_rules.check_already_recovered,
            stopping_rules.check_terminal_case,
            stopping_rules.check_case_expired,
            stopping_rules.check_max_attempts,
            stopping_rules.check_do_not_contact,
            guardrails.check_duplicate_action,
            # AI's non-binding recommend-stop / escalate / no-action signals.
            stopping_rules.check_ai_recommendation,
            # Per-workflow permission whitelist.
            check_permission,
            # Promise-to-pay lifecycle: never auto-approve a duplicate promise.
            stopping_rules.check_duplicate_promise,
            # Escalation thresholds.
            guardrails.check_amount_threshold,
            guardrails.check_low_confidence_high_value,
            # Transient guardrail.
            guardrails.check_cooldown,
        )

    def evaluate(
        self,
        proposal: ActionProposal,
        case: RecoveryCase,
        context: PolicyContext | None = None,
    ) -> PolicyDecision:
        """Return the single APPROVE / ESCALATE / STOP decision for this proposal."""
        ctx = context or PolicyContext()
        for rule in self._rules:
            outcome = rule(proposal, case, ctx, self.config)
            if outcome is not None:
                return self._decision(outcome, proposal, case)

        # No rule fired: the action is permitted and passed every guardrail.
        approved = RuleOutcome(
            decision=DecisionType.APPROVE,
            rule_id=RuleId.APPROVE_PERMITTED_ACTION,
            reason=(
                f"action {proposal.action_type.value!r} is permitted for workflow "
                f"{case.case_type.value!r} and passed all guardrails"
            ),
        )
        return self._decision(approved, proposal, case)

    # -- deterministic retry scheduling -------------------------------------------------
    #
    # These are pure helpers (no I/O, no AI). The orchestrator uses them to space retries on
    # the fixed schedule; the *timing* of a retry is therefore owned by policy, never by the
    # AI. They read only ``case.attempt_count`` (attempts already completed).

    def retry_delay_seconds(self, attempts_made: int) -> int | None:
        """Delay before the next attempt, or ``None`` when the schedule is exhausted."""
        return retry.retry_delay_seconds(attempts_made, self.config.retry_schedule_seconds)

    def next_retry_at(self, case: RecoveryCase, reference: datetime) -> datetime | None:
        """The next eligible retry time for ``case`` measured from ``reference``.

        ``None`` means no retry remains — the case must fall to the max-attempts STOP rather
        than being retried indefinitely.
        """
        return retry.next_retry_at(
            reference, case.attempt_count, self.config.retry_schedule_seconds
        )

    def has_retries_remaining(self, case: RecoveryCase) -> bool:
        """Whether the bounded schedule still permits another attempt for ``case``."""
        return retry.retries_remaining(
            case.attempt_count, self.config.retry_schedule_seconds
        )

    def _decision(
        self, outcome: RuleOutcome, proposal: ActionProposal, case: RecoveryCase
    ) -> PolicyDecision:
        return PolicyDecision(
            case_id=case.case_id,
            decision=outcome.decision,
            proposed_action=proposal.action_type,
            reason=outcome.reason,
            matched_rules=[outcome.rule_id.value],
            attempt_count=case.attempt_count,
            max_attempts=self.config.max_attempts,
            amount_at_risk=case.amount_at_risk,
            threshold_amount=outcome.threshold_amount,
        )


__all__ = ["PolicyEngine"]
