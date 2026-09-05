"""RazorpayVerifier — verified Razorpay payment status is the source of truth (Phase 9).

A successful executor/adapter call only means an order or payment link was *created*; it is
never recovery. This verifier independently confirms the outcome by reading the **verified
payment status** from Razorpay (the created object's ``status``), and marks a case RECOVERED
only when Razorpay itself reports the money as paid. Anything else stays PENDING (awaiting the
customer's payment / a ``*.paid`` webhook), and a technically failed action is NOT_RECOVERED.

The status can come from either transport described in ARCHITECTURE §7: **polling** (fetch the
object via the injected client) or a **webhook**-fed status map. Both are supplied through the
same ``OutcomeVerifier`` interface, so the orchestrator and policy gate are unchanged. The
client is injected; tests use a fake client and make no real calls.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from revguard.domain import (
    ActionProposal,
    ActionType,
    ExecutionStatus,
    RecoveryCase,
    RecoveryOutcome,
    RecoveryResult,
    VerificationStatus,
    utcnow,
)
from revguard.execution import ExecutionRecord
from revguard.verification import OutcomeVerifier

logger = logging.getLogger(__name__)

# Provider statuses that mean the money was actually collected.
_PAID_STATUSES: frozenset[str] = frozenset({"paid", "captured"})

# Which fetch to use per action (only payment-moving actions have a status to confirm).
_FETCH_ATTR: dict[ActionType, str] = {
    ActionType.RETRY_PAYMENT: "order",
    ActionType.CREATE_PAYMENT_LINK: "payment_link",
}


class RazorpayVerifier(OutcomeVerifier):
    """Confirms recovery from the verified Razorpay payment status."""

    name = "razorpay"

    def __init__(
        self,
        client: Any,
        *,
        status_source: Callable[[ExecutionRecord], dict[str, Any] | None] | None = None,
    ) -> None:
        self._client = client
        # Optional override (e.g. a webhook-fed store). Defaults to polling the client.
        self._status_source = status_source

    def verify(
        self, case: RecoveryCase, proposal: ActionProposal, execution: ExecutionRecord
    ) -> RecoveryResult:
        action = execution.action
        now = utcnow()

        # A failed action never recovers money.
        if execution.result.execution_status is ExecutionStatus.FAILED:
            return self._not_recovered(case, action, now, ExecutionStatus.FAILED)

        entity = self._fetch(execution)
        status = (entity or {}).get("status")

        if status in _PAID_STATUSES:
            amount = self._paid_amount(entity, case)
            reference = execution.adapter_result.reference or (entity or {}).get("id")
            if amount > 0 and reference:
                return RecoveryResult(
                    case_id=case.case_id,
                    action=action,
                    execution_status=ExecutionStatus.SUCCEEDED,
                    verification_status=VerificationStatus.RECOVERED,
                    outcome=RecoveryOutcome.RECOVERED,
                    amount_recovered=amount,
                    currency=case.currency,
                    payment_reference=str(reference),
                    verified_at=now,
                    created_at=now,
                )

        # Created but not (yet) paid, non-payment action, or unknown status → still pending.
        return RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=ExecutionStatus.SUCCEEDED,
            verification_status=VerificationStatus.PENDING,
            outcome=RecoveryOutcome.PENDING,
            amount_recovered=Decimal("0"),
            created_at=now,
        )

    # -- status retrieval ---------------------------------------------------------------

    def _fetch(self, execution: ExecutionRecord) -> dict[str, Any] | None:
        if self._status_source is not None:
            return self._status_source(execution)

        reference = execution.adapter_result.reference
        attr = _FETCH_ATTR.get(execution.action)
        if reference is None or attr is None:
            return None  # nothing on the provider to confirm (e.g. reminder/wait)
        try:
            resource = getattr(self._client, attr)
            entity = resource.fetch(reference)
        except Exception as exc:  # noqa: BLE001 - a fetch failure means "unconfirmed"
            logger.warning(
                "razorpay status fetch failed for %s: %s",
                execution.action.value,
                type(exc).__name__,
            )
            return None
        return entity if isinstance(entity, dict) else None

    @staticmethod
    def _paid_amount(entity: dict[str, Any] | None, case: RecoveryCase) -> Decimal:
        """Verified amount collected (paise → major units), capped at the amount at risk."""
        entity = entity or {}
        raw = entity.get("amount_paid")
        if raw is None:
            raw = entity.get("amount")
        if raw is None:
            amount = case.amount_at_risk
        else:
            amount = (Decimal(int(raw)) / Decimal(100)).quantize(Decimal("0.01"))
        return min(amount, case.amount_at_risk)

    @staticmethod
    def _not_recovered(
        case: RecoveryCase, action: ActionType, now, execution_status: ExecutionStatus
    ) -> RecoveryResult:
        return RecoveryResult(
            case_id=case.case_id,
            action=action,
            execution_status=execution_status,
            verification_status=VerificationStatus.NOT_RECOVERED,
            outcome=(
                RecoveryOutcome.ACTION_FAILED
                if execution_status is ExecutionStatus.FAILED
                else RecoveryOutcome.NOT_RECOVERED
            ),
            amount_recovered=Decimal("0"),
            failure_reason=f"{action.value} not confirmed as recovered by Razorpay",
            verified_at=now,
            created_at=now,
        )


__all__ = ["RazorpayVerifier"]
