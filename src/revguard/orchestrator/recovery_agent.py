"""RecoveryAgent — the deterministic end-to-end control loop (Stage 7).

Wires every prior stage into one bounded loop, per case:

    Event → Detector → RecoveryCase → Diagnoser → ActionProposal → PolicyEngine
    → (APPROVE ⇒ Executor) → OutcomeVerifier → RecoveryResult → update case/audit
    → continue or terminate

The agent owns **no** business rules of its own: it never chooses actions (the diagnoser
proposes, the policy engine decides) and never declares recovery (only the verifier does).
It only sequences the stages, enforces explicit state transitions, records the audit trail,
and stops when a terminal decision or the step budget is reached. Key invariants:

* nothing executes without a matching APPROVE (the executor re-checks this too);
* a malformed/failed diagnosis degrades to a recommend-escalation and cannot execute;
* recovered money comes only from the verifier's :class:`RecoveryResult`;
* terminal cases are never processed again, and every transition is explicit/validated;
* idempotency keys already executed are not executed again (no duplicate side effects).

It uses the existing repositories/audit abstractions and introduces no queues, schedulers,
or distributed infrastructure.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from revguard.audit import AuditActor, AuditLog, AuditStage
from revguard.detection.engine import DetectionEngine
from revguard.diagnosis import (
    Diagnoser,
    classify_exception,
    create_diagnoser,
    safe_fallback_proposal,
    user_message,
)
from revguard.domain import (
    EXECUTABLE_ACTIONS,
    TERMINAL_STATUSES,
    ActionProposal,
    ActionType,
    CaseStatus,
    Currency,
    DecisionType,
    EscalationRecord,
    Event,
    ExecutionStatus,
    PolicyDecision,
    PromiseStatus,
    PromiseToPay,
    RecoveryCase,
    RecoveryOutcome,
    RecoveryResult,
    RevenueRiskSignal,
    StopReason,
    VerificationStatus,
    WorkflowType,
    utcnow,
)
from revguard.execution import (
    ActionAdapter,
    ActionExecutor,
    AdapterResult,
    ExecutionRecord,
    MockAdapter,
)
from revguard.persistence import CaseRepository, Database, EventRepository
from revguard.policy import (
    CONTACT_ACTIONS,
    ActionRecord,
    PolicyConfig,
    PolicyContext,
    PolicyEngine,
    RuleId,
)
from revguard.verification import MockVerifier, OutcomeVerifier

logger = logging.getLogger(__name__)


class InvalidTransition(Exception):
    """Raised when the agent attempts a case-state transition that is not allowed."""


class HumanReviewError(Exception):
    """Raised when a human authorization is requested on a case that is not in review."""


# The rule id recorded when a *human operator* — not the deterministic policy engine —
# authorizes an action on an escalated case (the "human queue" decision).
_HUMAN_OVERRIDE_RULE = "human.override"

# Default horizon for a promise-to-pay when the proposal/signal carries no explicit date.
_DEFAULT_PROMISE_HORIZON = timedelta(days=7)

# How long a processing lock is honoured before it is treated as abandoned (e.g. the run
# crashed mid-execution) and may be stolen by a later run. This is the backstop that guarantees
# a case can never be *permanently* stuck locked; the normal path always releases in a finally.
_LOCK_TTL = timedelta(minutes=5)

# Terminal case statuses (string values) a claim must never acquire — precomputed once.
_TERMINAL_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in TERMINAL_STATUSES)


# Explicit, deterministic state machine. Terminal states map to no successors, so a
# terminal case can never transition (and is never processed) again.
_ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.DETECTED: frozenset(
        {CaseStatus.ANALYZING, CaseStatus.ESCALATED, CaseStatus.STOPPED}
    ),
    CaseStatus.ANALYZING: frozenset(
        {
            CaseStatus.ACTION_APPROVED,
            CaseStatus.ESCALATED,
            CaseStatus.STOPPED,
            CaseStatus.WAITING,
        }
    ),
    CaseStatus.ACTION_APPROVED: frozenset(
        {CaseStatus.ACTION_EXECUTING, CaseStatus.ESCALATED, CaseStatus.STOPPED}
    ),
    CaseStatus.ACTION_EXECUTING: frozenset(
        {
            CaseStatus.RECOVERED,
            CaseStatus.WAITING,
            CaseStatus.ACTION_PENDING,
            CaseStatus.FAILED,
            CaseStatus.ESCALATED,
            CaseStatus.STOPPED,
        }
    ),
    CaseStatus.ACTION_PENDING: frozenset(
        {CaseStatus.ANALYZING, CaseStatus.ESCALATED, CaseStatus.STOPPED}
    ),
    CaseStatus.WAITING: frozenset(
        {CaseStatus.ANALYZING, CaseStatus.ESCALATED, CaseStatus.STOPPED, CaseStatus.RECOVERED}
    ),
    # RECOVERED / STOPPED / FAILED are terminal — no successors.
    CaseStatus.RECOVERED: frozenset(),
    # ESCALATED is terminal for the *autonomous* loop (still in TERMINAL_STATUSES, so
    # process_case never re-runs it). The one exception is an explicit **human** authorization
    # from the review queue, which may re-enter execution — nothing automatic uses this edge.
    CaseStatus.ESCALATED: frozenset({CaseStatus.ACTION_APPROVED}),
    CaseStatus.STOPPED: frozenset(),
    CaseStatus.FAILED: frozenset(),
}

# Map a firing STOP rule to the case-level StopReason recorded on the case.
_STOP_REASONS: dict[str, StopReason] = {
    RuleId.STOP_MAX_ATTEMPTS.value: StopReason.MAX_ATTEMPTS_REACHED,
    RuleId.STOP_CASE_EXPIRED.value: StopReason.CASE_EXPIRED,
    RuleId.STOP_DO_NOT_CONTACT.value: StopReason.DO_NOT_CONTACT,
    RuleId.STOP_ALREADY_RECOVERED.value: StopReason.ALREADY_RECOVERED,
    RuleId.STOP_DUPLICATE_ACTION.value: StopReason.DUPLICATE_EVENT,
}


@dataclass(frozen=True)
class RecoveryConfirmation:
    """A verified, out-of-band confirmation that money was collected for a case.

    Built by an integration from a **already-verified** source (e.g. a signature-checked
    Razorpay *paid* webhook). It carries only normalised, provider-agnostic fields so the
    orchestrator — which owns case state — never imports an integration. Producing one of these
    is itself the act of verification: the reconciler trusts it exactly as it trusts the
    :class:`OutcomeVerifier`, and never as a mere "the API returned 200".
    """

    reference: str  # provider payment/order id — proof of collection (required)
    amount: Decimal | None = None
    currency: Currency | None = None
    case_reference: str | None = None  # explicit case id (from the entity notes), if present
    subscription_id: str | None = None
    order_id: str | None = None
    invoice_id: str | None = None
    payment_id: str | None = None
    source: str = "webhook"


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of reconciling a confirmation against stored cases."""

    matched: bool
    reconciled: bool
    reason: str
    case_id: str | None = None
    status: str | None = None
    amount_recovered: Decimal = Decimal("0")


class RecoveryAgent:
    """Runs recovery cases through the full deterministic pipeline."""

    def __init__(
        self,
        database: Database,
        *,
        diagnoser: Diagnoser | None = None,
        verifier: OutcomeVerifier | None = None,
        adapter: ActionAdapter | None = None,
        config: PolicyConfig | None = None,
        max_steps: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._db = database
        self._diagnoser = diagnoser or create_diagnoser()
        self._verifier = verifier or MockVerifier()
        self._adapter = adapter or MockAdapter()
        self._config = config or PolicyConfig()
        self._policy = PolicyEngine(self._config)
        self._detection = DetectionEngine()
        # Injectable clock so retry scheduling is deterministic/testable; defaults to real time.
        self._now = clock or utcnow
        # A hard loop bound so the control loop is always finite, independent of policy.
        self._max_steps = max_steps if max_steps is not None else self._config.max_attempts + 2
        # Idempotency keys already executed (process-local; no distributed store).
        self._executed_keys: set[str] = set()

    # -- public API ---------------------------------------------------------------------

    def process_events(self, events: Sequence[Event]) -> list[RecoveryCase]:
        """Detect risk from events and process each newly created case.

        Events and cases are deduplicated so repeating the same events does not create
        duplicate cases or trigger duplicate recovery work.
        """
        new_cases: list[RecoveryCase] = []
        with self._db.session() as s:
            event_repo = EventRepository(s)
            case_repo = CaseRepository(s)
            audit = AuditLog(s)
            for event in events:
                event_repo.add_if_absent(event)  # idempotent persistence
            for signal in self._detection.run(events):
                case = self._case_from_signal(signal)
                if case_repo.get(case.case_id) is not None:
                    continue  # a case for this subject already exists — no duplicate work
                case_repo.add(case)
                self._audit_case_created(audit, case)
                new_cases.append(case)

        for case in new_cases:
            self.process_case(case)
        return new_cases

    def ingest_event(self, event: Event) -> list[RecoveryCase]:
        """Persist one event, run detection, and create/update cases — but do NOT run the
        recovery workflow (that is :meth:`process_case`).

        Detection needs history, so it runs over all persisted events. New signals create a
        case (audited CASE_CREATED); an existing non-terminal case has its signal/amount
        refreshed. Deduplication by deterministic ``case_id`` keeps this idempotent, and no
        diagnoser/executor is touched, so ingestion needs no AI or payment credentials.
        """
        affected: list[RecoveryCase] = []
        with self._db.session() as s:
            event_repo = EventRepository(s)
            case_repo = CaseRepository(s)
            audit = AuditLog(s)
            event_repo.add_if_absent(event)  # idempotent persistence
            for signal in self._detection.run(event_repo.list_all()):
                case = self._case_from_signal(signal)
                existing = case_repo.get(case.case_id)
                if existing is None:
                    case_repo.add(case)
                    self._audit_case_created(audit, case)
                    affected.append(case)
                elif not existing.is_terminal and case.amount_at_risk >= existing.amount_recovered:
                    # Refresh a still-open case's risk view from the latest detection.
                    existing.signal = case.signal
                    existing.amount_at_risk = case.amount_at_risk
                    existing.updated_at = utcnow()
                    case_repo.save(existing)
                    affected.append(existing)
        return affected

    def reconcile_recovery(self, confirmation: RecoveryConfirmation) -> ReconciliationResult:
        """Reconcile a verified out-of-band collection to the case that initiated it.

        This is the webhook -> RecoveryCase link: a signature-verified *paid* Razorpay webhook
        is normalised into a :class:`RecoveryConfirmation` and passed here to confirm recovery
        on the matching case. Invariants preserved:

        * recovery is marked **only** from a verified confirmation (never from API success);
        * only a case that is *awaiting verification* (a state from which RECOVERED is a legal
          transition, i.e. WAITING/ACTION_EXECUTING) is confirmed — the deterministic state
          machine is never bypassed;
        * it is idempotent: a repeat confirmation for an already-terminal case is a no-op;
        * it never touches the diagnoser/executor, so it needs no AI/payment credentials.
        """
        with self._db.session() as s:
            case_repo = CaseRepository(s)
            audit = AuditLog(s)

            case = self._match_case(case_repo, confirmation)
            if case is None:
                return ReconciliationResult(
                    matched=False, reconciled=False, reason="no matching case"
                )
            if case.is_terminal:
                return ReconciliationResult(
                    matched=True,
                    reconciled=False,
                    reason=f"case already {case.status.value}",
                    case_id=case.case_id,
                    status=case.status.value,
                    amount_recovered=case.amount_recovered,
                )
            if CaseStatus.RECOVERED not in _ALLOWED_TRANSITIONS.get(case.status, frozenset()):
                # e.g. a case still DETECTED/ANALYZING: recording only, no forced transition.
                return ReconciliationResult(
                    matched=True,
                    reconciled=False,
                    reason=f"case not awaiting verification ({case.status.value})",
                    case_id=case.case_id,
                    status=case.status.value,
                )

            result = self._confirmation_result(case, confirmation)
            self._audit(
                audit,
                AuditStage.VERIFICATION,
                AuditActor.SYSTEM,
                case,
                action=case.current_action.value if case.current_action else None,
                details={
                    "verification_status": result.verification_status.value,
                    "execution_status": result.execution_status.value,
                    "amount_recovered": str(result.amount_recovered),
                    "source": confirmation.source,
                    "payment_reference": confirmation.reference,
                },
            )
            self._apply_outcome(case, result, case_repo, audit)
            return ReconciliationResult(
                matched=True,
                reconciled=True,
                reason="recovery confirmed",
                case_id=case.case_id,
                status=case.status.value,
                amount_recovered=case.amount_recovered,
            )

    def simulate_test_recovery(
        self, case: RecoveryCase, *, source: str = "razorpay_test_simulation"
    ) -> ReconciliationResult:
        """Simulate the customer completing the pending payment for a WAITING case.

        A WAITING case has had an action executed (a real Razorpay Test Mode order/link in
        production, or an offline demo test double), but recovery stays pending until a payment
        is confirmed — which, without a real customer, does not happen on its own. This drives
        that same confirmation through the **verified reconciliation path** a live ``*.paid``
        webhook would use (state machine + verification based, idempotent). Recovery is applied
        by :meth:`reconcile_recovery`, never fabricated. ``source`` records *how* it was
        confirmed so the audit/UI can label it truthfully (a real Razorpay Test Mode simulation
        vs a purely offline demo simulation). Callers must gate this to Test Mode / demo.
        """
        reference = self._last_execution_reference(case.case_id) or f"testpay_{case.case_id}"
        sig = case.signal
        confirmation = RecoveryConfirmation(
            reference=reference,
            amount=case.amount_at_risk,
            currency=case.currency,
            case_reference=case.case_id,
            subscription_id=sig.subscription_id,
            order_id=sig.order_id,
            invoice_id=sig.invoice_id,
            source=source,
        )
        return self.reconcile_recovery(confirmation)

    def verify_unpaid(
        self, case: RecoveryCase, *, source: str = "demo_unpaid_simulation"
    ) -> RecoveryCase:
        """Record that a WAITING case's pending payment was NOT received, then let policy decide.

        The honest counterpart to :meth:`simulate_test_recovery`: instead of confirming a
        payment, it produces a **NOT_RECOVERED** verification for the currently-pending
        intervention (no payment is invented and the recovered amount stays ``0``), records it
        in the audit trail, and re-enters the deterministic control loop. From there the
        existing PolicyEngine + bounded-retry logic — never a second retry system — decides the
        next step: another permitted attempt (back to WAITING) or a terminal STOP/ESCALATE once
        the action schedule/attempt budget is exhausted. Recovery is never fabricated here.

        ``source`` records *how* the non-payment was established so the audit/UI can label it
        truthfully (a deterministic demo test double vs a real provider "unpaid" status).
        Callers gate this to demo mode; a WAITING/ACTION_EXECUTING case is required (anything
        else is a no-op).
        """
        if case.is_terminal:
            return case
        self._ensure_case_row(case)
        if not self._claim(case.case_id):
            self._adopt_persisted(case)
            return case
        try:
            self._adopt_persisted(case)
            if case.is_terminal or case.status not in (
                CaseStatus.WAITING,
                CaseStatus.ACTION_EXECUTING,
            ):
                return case
            with self._db.session() as s:
                case_repo = CaseRepository(s)
                audit = AuditLog(s)
                result = self._unpaid_result(case)
                self._audit(
                    audit,
                    AuditStage.VERIFICATION,
                    AuditActor.SYSTEM,
                    case,
                    action=case.current_action.value if case.current_action else None,
                    details={
                        "verification_status": result.verification_status.value,
                        "execution_status": result.execution_status.value,
                        "amount_recovered": str(result.amount_recovered),
                        "source": source,
                        "reason": "customer did not pay; verified not recovered",
                    },
                )
                # WAITING -> ANALYZING is a legal transition; re-entering the loop hands the
                # next step back to the existing policy/retry logic (another bounded attempt,
                # or a terminal STOP/ESCALATE). The recovered amount is never touched here.
                self._transition(case, CaseStatus.ANALYZING)
                case_repo.save(case)
            self._run_loop(case)
        finally:
            self._release(case.case_id)
        return case

    def _unpaid_result(self, case: RecoveryCase) -> RecoveryResult:
        """A verified NOT_RECOVERED result for a pending intervention (no payment invented)."""
        now = utcnow()
        return RecoveryResult(
            case_id=case.case_id,
            action=case.current_action,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.NOT_RECOVERED,
            outcome=RecoveryOutcome.NOT_RECOVERED,
            amount_recovered=Decimal("0"),
            failure_reason="customer did not pay by verification",
            verified_at=now,
            created_at=now,
        )

    def recheck_payment(self, case: RecoveryCase) -> RecoveryCase:
        """Re-poll the provider's verified payment status for a WAITING case (no fabrication).

        The production counterpart to the demo simulations: it asks the configured
        :class:`OutcomeVerifier` for the *current* status of the case's last intervention — the
        polling transport the :class:`RazorpayVerifier` already supports, complementing the
        signature-verified ``*.paid`` webhook. It applies only the genuine outcome: a verified
        paid status recovers the case with the **verified** amount; anything else leaves it
        WAITING (still awaiting payment). Recovery is never invented, and no case state is forced
        — only a real RECOVERED verification transitions the case.
        """
        if case.is_terminal:
            return case
        self._ensure_case_row(case)
        if not self._claim(case.case_id):
            self._adopt_persisted(case)
            return case
        try:
            self._adopt_persisted(case)
            if case.is_terminal or case.status not in (
                CaseStatus.WAITING,
                CaseStatus.ACTION_EXECUTING,
            ):
                return case
            action = case.current_action
            if action is None:
                return case  # nothing was executed to re-check
            reference = self._last_execution_reference(case.case_id)
            with self._db.session() as s:
                case_repo = CaseRepository(s)
                audit = AuditLog(s)
                execution = self._rebuild_execution(case, action, reference)
                proposal = ActionProposal(
                    case_id=case.case_id,
                    action_type=action,
                    rationale="re-checking the provider's verified payment status",
                    confidence=1.0,
                )
                result = self._verifier.verify(case, proposal, execution)
                self._audit(
                    audit,
                    AuditStage.VERIFICATION,
                    AuditActor.SYSTEM,
                    case,
                    action=action.value,
                    details={
                        "verification_status": result.verification_status.value,
                        "execution_status": result.execution_status.value,
                        "amount_recovered": str(result.amount_recovered),
                        "source": "status_recheck",
                        "payment_reference": result.payment_reference,
                    },
                )
                # Apply only a genuine RECOVERED (WAITING -> RECOVERED). Any other status leaves
                # the case WAITING — this is a status read, never a retry driver or a fabrication.
                if result.is_recovered:
                    self._apply_outcome(case, result, case_repo, audit)
        finally:
            self._release(case.case_id)
        return case

    def _rebuild_execution(
        self, case: RecoveryCase, action: ActionType, reference: str | None
    ):
        """Reconstruct the technical ExecutionRecord for the last intervention, for a re-poll.

        Carries only the provider reference + action the verifier needs to fetch the current
        status; it asserts nothing about recovery (that is the verifier's job).
        """
        adapter_result = AdapterResult(
            action=action,
            accepted=True,
            succeeded=True,
            detail="provider status re-check",
            reference=reference,
            simulated=False,
        )
        pending = RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.PENDING,
            outcome=RecoveryOutcome.PENDING,
            amount_recovered=Decimal("0"),
        )
        return ExecutionRecord(
            case_id=case.case_id,
            decision_id="status_recheck",
            action=action,
            idempotency_key=f"{case.case_id}:status_recheck",
            adapter_result=adapter_result,
            result=pending,
        )

    def check_due_promise(self, case: RecoveryCase) -> RecoveryCase:
        """Verify a promise-to-pay on or after its promised time (no background scheduler).

        This is the manual/opportunistic trigger a later run, an operator, or a demo uses to
        advance the promise lifecycle: if the promised time has arrived, it checks the actual
        payment state via the existing verifier and either transitions the case to RECOVERED
        (promise kept) or routes a missed promise through the PolicyEngine escalation/stop path.
        Terminal cases and promises that are not yet due are left untouched.
        """
        with self._db.session() as s:
            case_repo = CaseRepository(s)
            audit = AuditLog(s)
            fresh = case_repo.get(case.case_id) or case
            self._resolve_promise(fresh, case_repo, audit)
            return fresh

    def _last_execution_reference(self, case_id: str) -> str | None:
        with self._db.session() as s:
            for entry in reversed(AuditLog(s).for_case(case_id)):
                if entry.stage is AuditStage.EXECUTION:
                    ref = entry.details.get("reference")
                    if isinstance(ref, str) and ref:
                        return ref
        return None

    def process_case(self, case: RecoveryCase) -> RecoveryCase:
        """Run the bounded control loop for one case until terminal or waiting.

        Execution is made atomic with an exclusive, DB-backed **claim**: the case is persisted,
        then a single caller atomically acquires its processing lock. A concurrent run of the
        same case fails to claim it and returns without executing a second intervention, so two
        simultaneous requests can never both act. The lock is always released in a ``finally``
        (and stale locks from a crashed run are stolen after ``_LOCK_TTL``), so a case can never
        become permanently stuck. Every prior safety check — idempotency, the PolicyEngine gate,
        amount caps, retry limits, stopping rules, audit, and verification — is unchanged.
        """
        if case.is_terminal:
            return case  # terminal cases are never processed again

        # Persist first so the case can be claimed; then claim it exclusively.
        self._ensure_case_row(case)
        if not self._claim(case.case_id):
            # A concurrent run already holds this case (or it just became terminal). Do NOT run
            # a second intervention — return the latest persisted state.
            self._adopt_persisted(case)
            return case

        try:
            # Operate on the authoritative persisted state under the lock — never a stale caller
            # copy — so a case another run already advanced is never re-processed from scratch.
            self._adopt_persisted(case)
            if case.is_terminal:
                return case
            self._run_loop(case)
        finally:
            self._release(case.case_id)
        return case

    def _run_loop(self, case: RecoveryCase) -> None:
        """The bounded control loop, run while the case's processing lock is held."""
        history: list[ActionRecord] = []
        with self._db.session() as s:
            case_repo = CaseRepository(s)
            audit = AuditLog(s)
            executor = ActionExecutor(self._adapter, audit=audit)
            self._ensure_persisted(case_repo, audit, case)

            for _ in range(self._max_steps):
                if case.is_terminal:
                    break
                if case.status is CaseStatus.WAITING:
                    # WAITING covers three cases: an open promise-to-pay awaiting its date, a
                    # deterministically *scheduled retry*, or a plain pending verification.
                    if case.promise is not None and case.promise.status.is_open:
                        # Verify the promise once its promised time has arrived; otherwise wait.
                        if self._now() >= case.promise.promised_at:
                            self._resolve_promise(case, case_repo, audit)
                        break
                    # Only a due scheduled retry resumes; otherwise stop and let a later run
                    # (with the persisted next_retry_at) pick it up.
                    if case.next_retry_at is None or self._now() < case.next_retry_at:
                        break
                    case.next_retry_at = None  # consume the marker; _step re-enters ANALYZING
                    case.updated_at = self._now()
                    case_repo.save(case)
                if not self._step(case, case_repo, audit, executor, history):
                    break

    def authorize(
        self, case: RecoveryCase, action: ActionType, *, operator: str = "operator"
    ) -> RecoveryCase:
        """Human-in-the-loop: an operator authorizes one bounded action on an ESCALATED case.

        This is the **only** path where a human — not the deterministic policy engine —
        sanctions an action, and it is audited as such (``actor=HUMAN``, rule
        ``human.override``). Every executor safeguard still applies (idempotency, the action
        whitelist, provider-backed execution) and recovery is still declared **only** by the
        verifier. A one-shot human action that neither recovers nor awaits confirmation is
        returned to the human queue rather than looping autonomously.
        """
        if case.status is not CaseStatus.ESCALATED:
            raise HumanReviewError(
                f"case {case.case_id} is {case.status.value}, not awaiting human review"
            )
        if action not in EXECUTABLE_ACTIONS:
            raise HumanReviewError(f"{action.value!r} is not an executable recovery action")
        # Consent/DND gate: a customer-contact action must never run without permission, even on
        # the human-authorization path. The autonomous PolicyEngine already blocks this (STOP
        # do_not_contact); we fail closed here too so a human cannot bypass the opt-out.
        if action in CONTACT_ACTIONS and case.do_not_contact:
            raise HumanReviewError(
                f"contact action {action.value!r} is blocked: customer opted out "
                f"(do_not_contact); consent is required before contacting the customer"
            )

        with self._db.session() as s:
            case_repo = CaseRepository(s)
            audit = AuditLog(s)
            executor = ActionExecutor(self._adapter, audit=audit)

            proposal = ActionProposal(
                case_id=case.case_id,
                action_type=action,
                rationale=f"Authorized by human operator ({operator}) after escalation",
                confidence=1.0,
            )
            decision = PolicyDecision(
                case_id=case.case_id,
                decision=DecisionType.APPROVE,
                proposed_action=action,
                reason=f"Human operator authorized {action.value} after escalation review",
                matched_rules=[_HUMAN_OVERRIDE_RULE],
            )
            # Record that a HUMAN (not the policy engine) authorized this action.
            self._audit(
                audit,
                AuditStage.POLICY_DECISION,
                AuditActor.HUMAN,
                case,
                action=DecisionType.APPROVE.value,
                details={
                    "proposed_action": action.value,
                    "matched_rules": [_HUMAN_OVERRIDE_RULE],
                    "reason": decision.reason,
                    "operator": operator,
                    "human_override": True,
                },
            )

            # Re-enter execution from the review queue, then verify + apply the outcome.
            self._transition(case, CaseStatus.ACTION_APPROVED)
            case_repo.save(case)

            record = executor.execute(decision, proposal, case)
            self._executed_keys.add(record.idempotency_key)
            if not record.from_cache:
                case.attempt_count += 1
                case.current_action = record.action
                case.current_step += 1
            self._transition(case, CaseStatus.ACTION_EXECUTING)
            case_repo.save(case)

            # A human-authorized promise-to-pay enters the same bounded lifecycle (PROMISED),
            # awaiting its date — never verified as recovery just for being recorded.
            if (
                record.action is ActionType.RECORD_PROMISE_TO_PAY
                and record.result.execution_status is ExecutionStatus.SUCCEEDED
            ):
                self._record_promise(case, proposal, record, case_repo, audit)
                return case

            result = self._verifier.verify(case, proposal, record)
            self._audit(
                audit,
                AuditStage.VERIFICATION,
                AuditActor.SYSTEM,
                case,
                action=record.action.value,
                details={
                    "verification_status": result.verification_status.value,
                    "execution_status": result.execution_status.value,
                    "amount_recovered": str(result.amount_recovered),
                },
            )
            self._apply_outcome(case, result, case_repo, audit)

            # Neither recovered nor awaiting a later check → hand back to the human queue.
            if case.status is CaseStatus.ACTION_PENDING:
                case.escalated_at = utcnow()
                case.escalation_reason = (
                    f"{action.value} did not recover; returned to human review"
                )
                self._transition(case, CaseStatus.ESCALATED)
                case_repo.save(case)
        return case

    # -- one step of the loop -----------------------------------------------------------

    def _step(
        self,
        case: RecoveryCase,
        repo: CaseRepository,
        audit: AuditLog,
        executor: ActionExecutor,
        history: list[ActionRecord],
    ) -> bool:
        """Run diagnosis → policy → (execute → verify). Returns True to continue looping."""
        self._transition(case, CaseStatus.ANALYZING)
        repo.save(case)

        proposal = self._diagnose(case)
        diagnosis_details: dict = {
            "provider": self._diagnoser.name,
            "confidence": proposal.confidence,
            "rationale": proposal.rationale,
        }
        # Record a provider failure (auth/quota/timeout/unavailable) in the audit trail — the
        # classification only, never a credential or raw provider payload.
        if proposal.evidence.get("fallback"):
            diagnosis_details["ai_failure"] = True
            failure_kind = proposal.evidence.get("failure_kind")
            if failure_kind is not None:
                diagnosis_details["failure_kind"] = failure_kind
        self._audit(
            audit,
            AuditStage.DIAGNOSIS,
            AuditActor.AI,
            case,
            action=proposal.action_type.value,
            details=diagnosis_details,
        )

        context = PolicyContext(
            now=self._now(),
            action_history=tuple(history),
            executed_idempotency_keys=frozenset(self._executed_keys),
        )
        decision = self._policy.evaluate(proposal, case, context)
        self._audit(
            audit,
            AuditStage.POLICY_DECISION,
            AuditActor.POLICY,
            case,
            action=decision.decision.value,
            details={
                "proposed_action": proposal.action_type.value,
                "matched_rules": list(decision.matched_rules),
                "reason": decision.reason,
            },
        )

        if decision.decision is DecisionType.ESCALATE:
            # When escalation is the result of an AI-provider failure (fail-closed), surface the
            # concise, classified failure reason on the case — not the generic policy phrasing.
            reason = proposal.rationale if proposal.evidence.get("fallback") else None
            self._escalate(case, proposal, decision, repo, audit, reason=reason)
            return False
        if decision.decision is DecisionType.STOP:
            # Intelligent retry sequencing: a *cooldown* stop on a still-recoverable case is
            # not terminal — it means "this action was retried too soon". Convert it into a
            # deterministically scheduled retry (held WAITING) instead of giving up, as long as
            # the bounded schedule still permits an attempt. Every other STOP stays terminal.
            if self._should_schedule_retry(proposal, case, decision):
                self._schedule_retry(case, proposal, decision, repo, audit)
                return False
            self._stop(case, decision, repo, audit)
            return False

        # APPROVE: execute (executor re-verifies the APPROVE and records the EXECUTION entry).
        self._transition(case, CaseStatus.ACTION_APPROVED)
        repo.save(case)
        record = executor.execute(decision, proposal, case)
        self._executed_keys.add(record.idempotency_key)
        if not record.from_cache:
            history.append(
                ActionRecord(
                    action_type=record.action,
                    occurred_at=self._now(),
                    idempotency_key=record.idempotency_key,
                )
            )
            case.attempt_count += 1
            case.current_action = record.action
            case.current_step += 1
        self._transition(case, CaseStatus.ACTION_EXECUTING)
        repo.save(case)

        # A recorded promise-to-pay is a *commitment*, not a payment. Capture its lifecycle
        # (PROMISED) and wait for the promised time — it must never be verified as recovery
        # merely because the recording action ran. Verification happens later, when due.
        if (
            record.action is ActionType.RECORD_PROMISE_TO_PAY
            and record.result.execution_status is ExecutionStatus.SUCCEEDED
        ):
            self._record_promise(case, proposal, record, repo, audit)
            return False

        # Verify the real outcome — the ONLY place recovery/amount is decided.
        result = self._verifier.verify(case, proposal, record)
        self._audit(
            audit,
            AuditStage.VERIFICATION,
            AuditActor.SYSTEM,
            case,
            action=record.action.value,
            details={
                "verification_status": result.verification_status.value,
                "execution_status": result.execution_status.value,
                "amount_recovered": str(result.amount_recovered),
            },
        )
        return self._apply_outcome(case, result, repo, audit)

    def _apply_outcome(
        self,
        case: RecoveryCase,
        result: RecoveryResult,
        repo: CaseRepository,
        audit: AuditLog,
    ) -> bool:
        if result.is_recovered:
            case.amount_recovered = result.amount_recovered  # from the verifier ONLY
            self._transition(case, CaseStatus.RECOVERED)
            repo.save(case)
            self._audit(
                audit,
                AuditStage.RECOVERY_RESULT,
                AuditActor.SYSTEM,
                case,
                action=CaseStatus.RECOVERED.value,
                details={
                    "amount_recovered": str(result.amount_recovered),
                    "currency": result.currency.value if result.currency else None,
                    "payment_reference": result.payment_reference,
                },
            )
            return False

        if result.verification_status is VerificationStatus.PENDING:
            self._transition(case, CaseStatus.WAITING)
            repo.save(case)
            return False  # non-terminal; awaiting a later verification check

        # Not recovered — let the loop try the next step; policy enforces the budget/rules.
        self._transition(case, CaseStatus.ACTION_PENDING)
        repo.save(case)
        return True

    # -- terminal branches --------------------------------------------------------------

    def _escalate(
        self,
        case: RecoveryCase,
        proposal: ActionProposal,
        decision: PolicyDecision,
        repo: CaseRepository,
        audit: AuditLog,
        *,
        reason: str | None = None,
    ) -> None:
        escalation_reason = reason or decision.reason
        case.escalated_at = utcnow()
        case.escalation_reason = escalation_reason
        self._transition(case, CaseStatus.ESCALATED)
        repo.save(case)
        record = EscalationRecord(
            case_id=case.case_id,
            reason=escalation_reason,
            amount_at_risk=case.amount_at_risk,
            currency=case.currency,
            customer_id=case.customer_id,
            ai_recommended_action=proposal.action_type,
            ai_rationale=proposal.rationale,
        )
        self._audit(
            audit,
            AuditStage.ESCALATION,
            AuditActor.POLICY,
            case,
            action=DecisionType.ESCALATE.value,
            details={
                "escalation_id": record.escalation_id,
                "reason": escalation_reason,
                "matched_rules": list(decision.matched_rules),
                "ai_recommended_action": proposal.action_type.value,
                "amount_at_risk": str(case.amount_at_risk),
            },
        )

    def _stop(
        self,
        case: RecoveryCase,
        decision: PolicyDecision,
        repo: CaseRepository,
        audit: AuditLog,
    ) -> None:
        case.stopped_at = utcnow()
        case.stop_reason = self._stop_reason(decision)
        self._transition(case, CaseStatus.STOPPED)
        repo.save(case)
        self._audit(
            audit,
            AuditStage.STOP,
            AuditActor.POLICY,
            case,
            action=DecisionType.STOP.value,
            details={
                "reason": decision.reason,
                "matched_rules": list(decision.matched_rules),
                "stop_reason": case.stop_reason.value,
            },
        )

    # -- promise-to-pay lifecycle -------------------------------------------------------

    def _record_promise(
        self,
        case: RecoveryCase,
        proposal: ActionProposal,
        record: ExecutionRecord,
        repo: CaseRepository,
        audit: AuditLog,
    ) -> None:
        """Persist a recorded promise (PROMISED) and hold the case until the promised time.

        Duplicate promises are already blocked deterministically by the PolicyEngine, but this
        also refuses to overwrite an open promise as a defensive guard.
        """
        now = self._now()
        if case.promise is not None and case.promise.status.is_open:
            self._transition(case, CaseStatus.WAITING)
            repo.save(case)
            return
        promised_at = self._parse_promised_at(proposal, case, now)
        case.promise = PromiseToPay(
            case_id=case.case_id,
            promised_at=promised_at,
            recorded_at=now,
            status=PromiseStatus.PROMISED,
            amount=case.amount_at_risk,
            currency=case.currency,
            reference=record.adapter_result.reference,
        )
        self._transition(case, CaseStatus.WAITING)
        repo.save(case)
        self._audit(
            audit,
            AuditStage.STATUS_CHANGE,
            AuditActor.SYSTEM,
            case,
            action="promise_recorded",
            details={
                "promise_status": PromiseStatus.PROMISED.value,
                "promised_at": promised_at.isoformat(),
                "case_reference": case.case_id,
                "reference": record.adapter_result.reference,
                "amount": str(case.amount_at_risk),
            },
        )

    def _parse_promised_at(
        self, proposal: ActionProposal, case: RecoveryCase, now: datetime
    ) -> datetime:
        """Determine the promised payment time: proposal parameter → signal evidence → default.

        Timing is never invented as recovery; this only fixes *when* the promise is due. A naive
        (tz-less) date is assumed to be UTC; anything unparseable falls back to the default horizon.
        """
        for raw in (
            proposal.parameters.get("promised_date"),
            proposal.parameters.get("promised_at"),
            case.signal.evidence.get("promised_date"),
        ):
            parsed = _parse_iso(raw)
            if parsed is not None:
                return parsed
        return now + _DEFAULT_PROMISE_HORIZON

    def _resolve_promise(
        self, case: RecoveryCase, repo: CaseRepository, audit: AuditLog
    ) -> None:
        """Advance an open promise: verify payment when due, else leave it waiting.

        Never acts on a terminal case. An already-RECOVERED case marks the promise KEPT
        (idempotent); a not-yet-due promise is left PROMISED.
        """
        promise = case.promise
        if promise is None or not promise.status.is_open:
            return
        if case.status is CaseStatus.RECOVERED:  # paid out-of-band (e.g. webhook) before check
            self._mark_promise(case, PromiseStatus.KEPT, repo, audit, reference=None)
            return
        if case.is_terminal:  # never verify/act on a terminal case
            return
        now = self._now()
        if now < promise.promised_at:
            return  # not due yet — the commitment still stands

        # Due: enter PENDING and check the *actual* payment state via the existing verifier.
        if promise.status is not PromiseStatus.PENDING:
            case.promise = promise.model_copy(update={"status": PromiseStatus.PENDING})
            repo.save(case)
            self._audit(
                audit,
                AuditStage.STATUS_CHANGE,
                AuditActor.SYSTEM,
                case,
                action="promise_due",
                details={
                    "promise_status": PromiseStatus.PENDING.value,
                    "promised_at": promise.promised_at.isoformat(),
                },
            )

        result = self._verify_promise_payment(case)
        self._audit(
            audit,
            AuditStage.VERIFICATION,
            AuditActor.SYSTEM,
            case,
            action=ActionType.RECORD_PROMISE_TO_PAY.value,
            details={
                "verification_status": result.verification_status.value,
                "execution_status": result.execution_status.value,
                "amount_recovered": str(result.amount_recovered),
                "promise_status": PromiseStatus.PENDING.value,
            },
        )

        if result.is_recovered:
            case.amount_recovered = result.amount_recovered  # from the verifier ONLY
            self._mark_promise(
                case, PromiseStatus.KEPT, repo, audit, reference=result.payment_reference
            )
            self._transition(case, CaseStatus.RECOVERED)
            repo.save(case)
            self._audit(
                audit,
                AuditStage.RECOVERY_RESULT,
                AuditActor.SYSTEM,
                case,
                action=CaseStatus.RECOVERED.value,
                details={
                    "amount_recovered": str(result.amount_recovered),
                    "currency": result.currency.value if result.currency else None,
                    "payment_reference": result.payment_reference,
                    "via": "promise_to_pay",
                },
            )
            return

        # Missed: record it and route through the PolicyEngine-controlled escalate/stop path.
        self._mark_promise(case, PromiseStatus.MISSED, repo, audit, reference=None)
        self._resolve_missed_promise(case, repo, audit)

    def _mark_promise(
        self,
        case: RecoveryCase,
        status: PromiseStatus,
        repo: CaseRepository,
        audit: AuditLog,
        *,
        reference: str | None,
    ) -> None:
        if case.promise is None:
            return
        updates: dict = {"status": status}
        if status in (PromiseStatus.KEPT, PromiseStatus.MISSED):
            updates["resolved_at"] = self._now()
        if reference:
            updates["verification_reference"] = reference
        case.promise = case.promise.model_copy(update=updates)
        repo.save(case)
        self._audit(
            audit,
            AuditStage.STATUS_CHANGE,
            AuditActor.SYSTEM,
            case,
            action=f"promise_{status.value}",
            details={"promise_status": status.value, "case_reference": case.case_id},
        )

    def _verify_promise_payment(self, case: RecoveryCase) -> RecoveryResult:
        """Check the actual payment state for a due promise using the existing verifier.

        The verifier remains the sole authority on recovery: a promise is only ever KEPT when a
        payment is independently verified (e.g. a Razorpay *paid* status / webhook), never by the
        mere existence of the promise.
        """
        promise = case.promise
        proposal = ActionProposal(
            case_id=case.case_id,
            action_type=ActionType.RECORD_PROMISE_TO_PAY,
            rationale="promise-to-pay is due; verifying the actual payment state",
            confidence=1.0,
        )
        adapter_result = AdapterResult(
            action=ActionType.RECORD_PROMISE_TO_PAY,
            accepted=True,
            succeeded=True,
            detail="promise-to-pay verification check",
            reference=promise.reference if promise else None,
            simulated=True,
        )
        pending = RecoveryResult(
            case_id=case.case_id,
            action=ActionType.RECORD_PROMISE_TO_PAY,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.PENDING,
            outcome=RecoveryOutcome.PENDING,
            amount_recovered=Decimal("0"),
        )
        execution = ExecutionRecord(
            case_id=case.case_id,
            decision_id="promise_verify",
            action=ActionType.RECORD_PROMISE_TO_PAY,
            idempotency_key=f"{case.case_id}:promise_verify",
            adapter_result=adapter_result,
            result=pending,
        )
        return self._verifier.verify(case, proposal, execution)

    def _resolve_missed_promise(
        self, case: RecoveryCase, repo: CaseRepository, audit: AuditLog
    ) -> None:
        """A missed promise transitions through the deterministic PolicyEngine escalate/stop path.

        The orchestrator does not decide the outcome itself: it presents a deterministic
        recommend-escalation to the PolicyEngine, which returns ESCALATE (route to human) or a
        terminal STOP if a stopping rule (e.g. attempts/expiry) fires first.
        """
        proposal = ActionProposal(
            case_id=case.case_id,
            action_type=ActionType.RECOMMEND_ESCALATION,
            rationale="promise-to-pay was missed; recommending escalation to human review",
            confidence=1.0,
        )
        context = PolicyContext(
            now=self._now(),
            executed_idempotency_keys=frozenset(self._executed_keys),
        )
        decision = self._policy.evaluate(proposal, case, context)
        self._audit(
            audit,
            AuditStage.POLICY_DECISION,
            AuditActor.POLICY,
            case,
            action=decision.decision.value,
            details={
                "proposed_action": proposal.action_type.value,
                "matched_rules": list(decision.matched_rules),
                "reason": decision.reason,
                "trigger": "promise_missed",
            },
        )
        reason = "promise-to-pay was missed (no verified payment by the promised date)"
        if decision.decision is DecisionType.STOP:
            self._stop(case, decision, repo, audit)
        else:  # ESCALATE (or any non-terminal decision) → route to human review, fail closed
            self._escalate(case, proposal, decision, repo, audit, reason=reason)

    # -- retry sequencing ---------------------------------------------------------------

    def _should_schedule_retry(
        self, proposal: ActionProposal, case: RecoveryCase, decision: PolicyDecision
    ) -> bool:
        """A cooldown STOP on a recoverable case becomes a scheduled retry (not a give-up).

        Only a *cooldown* stop is reschedulable — every other STOP (max attempts, already
        recovered, expired, duplicate, do-not-contact, AI recommend-stop) stays terminal. The
        bounded schedule must still permit an attempt, and the AI must have proposed a real
        executable recovery action (never a recommend-* signal).
        """
        if RuleId.STOP_COOLDOWN_ACTIVE.value not in decision.matched_rules:
            return False
        if proposal.action_type not in EXECUTABLE_ACTIONS:
            return False
        return self._policy.has_retries_remaining(case)

    def _schedule_retry(
        self,
        case: RecoveryCase,
        proposal: ActionProposal,
        decision: PolicyDecision,
        repo: CaseRepository,
        audit: AuditLog,
    ) -> None:
        """Hold the case for its next deterministically scheduled retry (non-terminal WAITING).

        The next eligible time comes solely from the PolicyEngine's fixed schedule (the AI has
        no say in timing). It is persisted on the case so the retry survives a restart and is
        resumed by a later :meth:`process_case` once due.
        """
        now = self._now()
        next_at = self._policy.next_retry_at(case, now)
        if next_at is None:  # schedule exhausted — fall back to a terminal stop (defensive)
            self._stop(case, decision, repo, audit)
            return
        case.next_retry_at = next_at
        self._transition(case, CaseStatus.WAITING)
        repo.save(case)
        self._audit(
            audit,
            AuditStage.STATUS_CHANGE,
            AuditActor.POLICY,
            case,
            action="retry_scheduled",
            details={
                "attempt": case.attempt_count,
                "next_attempt": case.attempt_count + 1,
                "max_attempts": self._config.max_attempts,
                "delay_seconds": self._policy.retry_delay_seconds(case.attempt_count),
                "next_retry_at": next_at.isoformat(),
                "proposed_action": proposal.action_type.value,
                "reason": decision.reason,
            },
        )

    # -- helpers ------------------------------------------------------------------------

    def _diagnose(self, case: RecoveryCase) -> ActionProposal:
        """Obtain a validated proposal; any failure degrades to a safe escalation."""
        try:
            proposal = self._diagnoser.diagnose(case)
        except Exception as exc:  # a broken provider must never execute anything
            kind = classify_exception(exc)
            logger.warning(
                "diagnoser failed for case %s (kind=%s, type=%s); escalating",
                case.case_id,
                kind.value,
                type(exc).__name__,
            )
            return safe_fallback_proposal(
                case, reason=user_message(kind), failure_kind=kind.value
            )
        if not isinstance(proposal, ActionProposal) or proposal.case_id != case.case_id:
            logger.warning("invalid proposal for case %s; escalating", case.case_id)
            return safe_fallback_proposal(case, reason="invalid AI proposal; escalating")
        return proposal

    def _ensure_persisted(
        self, repo: CaseRepository, audit: AuditLog, case: RecoveryCase
    ) -> None:
        if repo.get(case.case_id) is None:
            repo.add(case)
            self._audit_case_created(audit, case)
        else:
            repo.save(case)

    # -- atomic claim / concurrency control ---------------------------------------------

    def _ensure_case_row(self, case: RecoveryCase) -> None:
        """Persist the case (idempotently) so it can be claimed, auditing only a real insert.

        Runs in its own committed transaction so a subsequent claim can target the row.
        Concurrent inserts are tolerated: only the winner records CASE_CREATED.
        """
        with self._db.session() as s:
            if CaseRepository(s).add_if_absent(case):
                self._audit_case_created(AuditLog(s), case)

    def _claim(self, case_id: str) -> bool:
        """Atomically acquire the case's processing lock in its own transaction.

        Returns True only for the single run that acquires it; a concurrent run gets False and
        must not execute. Committing here makes the lock immediately visible to other runs.
        """
        now = self._now()
        with self._db.session() as s:
            return CaseRepository(s).try_claim(
                case_id,
                now=now,
                stale_before=now - _LOCK_TTL,
                terminal_statuses=_TERMINAL_STATUS_VALUES,
            )

    def _release(self, case_id: str) -> None:
        """Release the case's processing lock (own transaction; always safe in a finally)."""
        with self._db.session() as s:
            CaseRepository(s).release(case_id)

    def _adopt_persisted(self, case: RecoveryCase) -> None:
        """Refresh ``case`` in place from the authoritative persisted row (if present).

        Copies the validated row state into the existing object without re-running per-field
        validators (both are already-validated ``RecoveryCase`` values), so a caller's stale
        copy reflects the current DB state before/after a claim.
        """
        with self._db.session() as s:
            fresh = CaseRepository(s).get(case.case_id)
        if fresh is None:
            return
        case.__dict__.update(fresh.__dict__)
        case.__pydantic_fields_set__.update(fresh.__pydantic_fields_set__)

    def _audit_case_created(self, audit: AuditLog, case: RecoveryCase) -> None:
        self._audit(
            audit,
            AuditStage.CASE_CREATED,
            AuditActor.SYSTEM,
            case,
            action=case.case_type.value,
            details={
                "risk_level": case.signal.risk_level.value,
                "amount_at_risk": str(case.amount_at_risk),
                "currency": case.currency.value,
            },
        )

    def _transition(self, case: RecoveryCase, new_status: CaseStatus) -> None:
        if new_status is case.status:
            return
        if new_status not in _ALLOWED_TRANSITIONS.get(case.status, frozenset()):
            raise InvalidTransition(
                f"case {case.case_id}: cannot transition "
                f"{case.status.value!r} -> {new_status.value!r}"
            )
        case.status = new_status
        case.updated_at = utcnow()

    @staticmethod
    def _stop_reason(decision: PolicyDecision) -> StopReason:
        for rule in decision.matched_rules:
            if rule in _STOP_REASONS:
                return _STOP_REASONS[rule]
        return StopReason.POLICY_STOP

    def _audit(
        self,
        audit: AuditLog,
        stage: AuditStage,
        actor: AuditActor,
        case: RecoveryCase,
        *,
        action: str | None = None,
        details: dict | None = None,
    ) -> None:
        audit.record_event(
            stage=stage,
            actor=actor,
            case_id=case.case_id,
            action=action,
            details=details or {},
            correlation_id=case.signal.signal_id,
        )

    def _match_case(
        self, repo: CaseRepository, c: RecoveryConfirmation
    ) -> RecoveryCase | None:
        """Find the case a confirmation belongs to.

        Prefers the explicit ``case_reference`` (the ``notes.case_id`` the executor stamped on
        the order/link); otherwise falls back to matching any shared entity identifier against
        the case's originating signal.
        """
        if c.case_reference:
            direct = repo.get(c.case_reference)
            if direct is not None:
                return direct
        for case in repo.list_all():
            sig = case.signal
            pairs = (
                (c.subscription_id, sig.subscription_id),
                (c.order_id, sig.order_id),
                (c.invoice_id, sig.invoice_id),
                (c.payment_id, sig.payment_id),
            )
            if any(a and b and a == b for a, b in pairs):
                return case
        return None

    def _confirmation_result(
        self, case: RecoveryCase, c: RecoveryConfirmation
    ) -> RecoveryResult:
        """Build the verified :class:`RecoveryResult` a confirmation represents.

        The recovered amount is the confirmed amount, capped at the case's amount at risk (and
        falling back to the full amount at risk if the provider omitted it). A confirmation is,
        by construction, a verified collection — hence ``verification_status=RECOVERED``.
        """
        amount = c.amount if (c.amount is not None and c.amount > 0) else case.amount_at_risk
        amount = min(amount, case.amount_at_risk)
        outcome = (
            RecoveryOutcome.RECOVERED
            if amount >= case.amount_at_risk
            else RecoveryOutcome.PARTIALLY_RECOVERED
        )
        return RecoveryResult(
            case_id=case.case_id,
            action=case.current_action,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.RECOVERED,
            outcome=outcome,
            amount_recovered=amount,
            currency=c.currency or case.currency,
            payment_reference=c.reference,
            verified_at=utcnow(),
        )

    def _case_from_signal(self, signal: RevenueRiskSignal) -> RecoveryCase:
        subject = (
            signal.subscription_id
            or signal.order_id
            or signal.invoice_id
            or signal.payment_id
            or signal.customer_id
        )
        if subject is None and signal.signal_type is WorkflowType.PAYMENT_DEGRADATION:
            # Payment degradation is method-wide and carries no entity id, so the random
            # ``signal_id`` would make a *new* case id on every detection run — spawning
            # duplicate cases and double-counting the same at-risk money in the metrics.
            # Key the case deterministically on the degraded method + currency so repeated
            # detections update the ONE case for that degradation.
            method = str(signal.evidence.get("method") or "unknown")
            subject = f"method_{method}_{signal.currency.value}"
        subject = subject or signal.signal_id
        return RecoveryCase(
            case_id=f"case_{signal.signal_type.value}_{subject}",
            case_type=signal.signal_type,
            customer_id=signal.customer_id,
            signal=signal,
            amount_at_risk=signal.amount_at_risk,
            currency=signal.currency,
        )


def _parse_iso(raw: object) -> datetime | None:
    """Parse an ISO-8601 date/datetime into an aware UTC datetime, or ``None`` if unusable."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from datetime import UTC

        parsed = parsed.replace(tzinfo=UTC)
    return parsed


__all__ = ["RecoveryAgent", "InvalidTransition"]
