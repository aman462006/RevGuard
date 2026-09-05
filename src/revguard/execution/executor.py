"""ActionExecutor — runs an action ONLY behind an APPROVE decision (Phase 6).

The executor is the stage that finally performs a business action, and it is deliberately
constrained:

* **Single gate.** It executes only when ``decision.decision is APPROVE`` *and* the APPROVE
  references the exact case + action being run (POLICY_SPEC §1 "executor enforcement"). Any
  ESCALATE/STOP, or any mismatch, raises :class:`ExecutionRejected` — no side effect occurs.
* **No arbitrary execution.** It dispatches through an :class:`ActionAdapter`, which only
  understands the finite whitelist of :class:`ActionType` recovery actions.
* **No AI.** It imports nothing from the diagnosis/LLM layer; it consumes a validated
  ``ActionProposal`` + ``PolicyDecision`` and never calls a model.
* **Idempotent.** Each execution is keyed by an idempotency key; a repeat with the same key
  returns the recorded outcome without invoking the adapter again.
* **Recovery ≠ execution.** A technically successful action yields a :class:`RecoveryResult`
  with ``execution_status = SUCCEEDED`` but ``verification_status = PENDING`` and
  ``amount_recovered = 0``. Actual recovery is confirmed only by verification (Phase 7).

Audit logging is optional and reached only through the :class:`AuditSink` protocol, so the
executor stays independent of the database/persistence implementation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from revguard.audit.models import AuditActor, AuditStage
from revguard.domain import (
    ActionProposal,
    ActionType,
    DecisionType,
    ExecutionStatus,
    PolicyDecision,
    RecoveryCase,
    RecoveryOutcome,
    RecoveryResult,
    VerificationStatus,
    utcnow,
)
from revguard.execution.adapter import ActionAdapter, AdapterResult
from revguard.execution.errors import ExecutionRejected


@runtime_checkable
class AuditSink(Protocol):
    """Structural interface the executor uses to record audit events.

    Matches :meth:`revguard.audit.AuditLog.record_event`, so a real ``AuditLog`` satisfies
    it — but the executor depends only on this protocol, never on a DB/session type.
    """

    def record_event(
        self,
        *,
        stage: AuditStage,
        actor: AuditActor,
        case_id: str | None = ...,
        action: str | None = ...,
        details: dict | None = ...,
        correlation_id: str | None = ...,
    ) -> Any: ...


@dataclass(frozen=True)
class ExecutionRecord:
    """What the executor returns, keeping the three facts of an attempt distinct (Phase 6 §4):

    * **accepted / executed** — ``result.execution_status`` (SUCCEEDED / FAILED).
    * **simulated action outcome** — ``adapter_result`` (technical, ``simulated=True``).
    * **actual payment recovery** — ``result.verification_status`` / ``result.amount_recovered``
      (deferred to verification in Phase 7: PENDING and 0 here).

    ``from_cache`` is True when the record was served via idempotency (the adapter was NOT
    invoked again).
    """

    case_id: str
    decision_id: str
    action: ActionType
    idempotency_key: str
    adapter_result: AdapterResult
    result: RecoveryResult
    from_cache: bool = False


class ActionExecutor:
    """Executes approved actions, enforces idempotency, and records outcomes."""

    def __init__(
        self,
        adapter: ActionAdapter,
        *,
        audit: AuditSink | None = None,
        clock: Callable[[], Any] = utcnow,
    ) -> None:
        self._adapter = adapter
        self._audit = audit
        self._clock = clock
        # idempotency key -> the record produced the first time it executed.
        self._records: dict[str, ExecutionRecord] = {}

    def execute(
        self,
        decision: PolicyDecision,
        proposal: ActionProposal,
        case: RecoveryCase,
        *,
        correlation_id: str | None = None,
    ) -> ExecutionRecord:
        """Execute the proposed action iff ``decision`` is a matching APPROVE.

        Raises :class:`ExecutionRejected` for any non-APPROVE decision or any mismatch
        between the decision, proposal, and case.
        """
        self._enforce_gate(decision, proposal, case, correlation_id)

        action = proposal.action_type
        key = self._idempotency_key(proposal, case)

        # Idempotency: never perform the same keyed action twice.
        cached = self._records.get(key)
        if cached is not None:
            return replace(cached, from_cache=True)

        # The adapter only understands the whitelist; an unsupported action raises.
        adapter_result = self._adapter.perform(action, case, proposal)
        result = self._to_result(action, case, adapter_result)

        record = ExecutionRecord(
            case_id=case.case_id,
            decision_id=decision.decision_id,
            action=action,
            idempotency_key=key,
            adapter_result=adapter_result,
            result=result,
        )
        self._records[key] = record
        self._audit_executed(record, correlation_id)
        return record

    # -- gate + consistency -------------------------------------------------------------

    def _enforce_gate(
        self,
        decision: PolicyDecision,
        proposal: ActionProposal,
        case: RecoveryCase,
        correlation_id: str | None,
    ) -> None:
        # Fail closed on any inconsistency between the three inputs.
        if not (decision.case_id == proposal.case_id == case.case_id):
            self._reject(decision, proposal, case, "case_id mismatch across inputs", correlation_id)
        if decision.proposed_action is not proposal.action_type:
            self._reject(
                decision,
                proposal,
                case,
                "APPROVE does not reference the proposed action",
                correlation_id,
            )
        if decision.decision is not DecisionType.APPROVE:
            self._reject(
                decision,
                proposal,
                case,
                f"policy decision is {decision.decision.value}, not APPROVE",
                correlation_id,
            )

    def _reject(
        self,
        decision: PolicyDecision,
        proposal: ActionProposal,
        case: RecoveryCase,
        reason: str,
        correlation_id: str | None,
    ) -> None:
        if self._audit is not None:
            self._audit.record_event(
                stage=AuditStage.EXECUTION,
                actor=AuditActor.EXECUTOR,
                case_id=case.case_id,
                action=proposal.action_type.value,
                details={
                    "rejected": True,
                    "reason": reason,
                    "decision": decision.decision.value,
                    "decision_id": decision.decision_id,
                },
                correlation_id=correlation_id,
            )
        raise ExecutionRejected(f"execution rejected for case {case.case_id}: {reason}")

    # -- result construction ------------------------------------------------------------

    def _to_result(
        self, action: ActionType, case: RecoveryCase, adapter_result: AdapterResult
    ) -> RecoveryResult:
        now = self._clock()
        if adapter_result.succeeded:
            # Action ran technically. Recovery is UNKNOWN until verified — never claim money.
            return RecoveryResult(
                case_id=case.case_id,
                action=action,
                execution_status=ExecutionStatus.SUCCEEDED,
                verification_status=VerificationStatus.PENDING,
                outcome=RecoveryOutcome.PENDING,
                amount_recovered=Decimal("0"),
                created_at=now,
            )
        return RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=ExecutionStatus.FAILED,
            verification_status=VerificationStatus.UNVERIFIED,
            outcome=RecoveryOutcome.ACTION_FAILED,
            amount_recovered=Decimal("0"),
            failure_reason=adapter_result.failure_reason or f"{action.value} failed",
            created_at=now,
        )

    # -- helpers ------------------------------------------------------------------------

    @staticmethod
    def _idempotency_key(proposal: ActionProposal, case: RecoveryCase) -> str:
        supplied = proposal.parameters.get("idempotency_key")
        if isinstance(supplied, str) and supplied:
            return supplied
        return f"{case.case_id}:{proposal.action_type.value}:{case.attempt_count}"

    def _audit_executed(self, record: ExecutionRecord, correlation_id: str | None) -> None:
        if self._audit is None:
            return
        ar = record.adapter_result
        self._audit.record_event(
            stage=AuditStage.EXECUTION,
            actor=AuditActor.EXECUTOR,
            case_id=record.case_id,
            action=record.action.value,
            details={
                "execution_status": record.result.execution_status.value,
                "verification_status": record.result.verification_status.value,
                # Ground-truth provider that actually performed the action (e.g. "mock" for the
                # offline demo test double, "razorpay_test" for a real Razorpay Test Mode call),
                # so the UI can label the provider truthfully instead of assuming Razorpay.
                "provider": self._adapter.name,
                "simulated": ar.simulated,
                "succeeded": ar.succeeded,
                "reference": ar.reference,
                # Customer-facing collection URL (e.g. a Razorpay Payment Link), when present.
                "url": ar.url,
                "idempotency_key": record.idempotency_key,
                "decision_id": record.decision_id,
                # Explicitly record that recovery is not yet confirmed here.
                "amount_recovered": str(record.result.amount_recovered),
            },
            correlation_id=correlation_id,
        )


__all__ = ["ActionExecutor", "ExecutionRecord", "AuditSink"]
