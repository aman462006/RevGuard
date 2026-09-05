"""RazorpayTestAdapter — routes approved payment actions to Razorpay Test Mode (Phase 9).

It implements the existing :class:`~revguard.execution.ActionAdapter` contract, so the
executor drives it exactly like the :class:`~revguard.execution.MockAdapter`: one method per
whitelisted action, no arbitrary execution. Only the two **payment-moving** actions have a
genuine Razorpay Test Mode primitive, so only those hit the API:

* ``retry_payment``       → ``client.order.create`` (a fresh order to collect the amount)
* ``create_payment_link`` → ``client.payment_link.create``

The remaining actions (``send_reminder``, ``record_promise_to_pay``, ``wait``) are not
Razorpay payment operations, so they are delegated to a ``fallback`` adapter (the MockAdapter
by default) rather than inventing Razorpay behaviour for them.

Crucially, a successful API call is **not** treated as recovered money: the adapter reports
only a *technical* :class:`~revguard.execution.AdapterResult` (``simulated=False``, a provider
reference). Whether money was actually recovered is decided by the RazorpayVerifier from the
verified payment status — never inferred here. The client is injected, so tests use a fake
client and no real payment call is ever made. Errors are reported by *type* only; the client,
credentials, and raw provider payloads are never placed in a failure message.
"""

from __future__ import annotations

import hashlib
import logging
from decimal import Decimal
from typing import Any

from revguard.domain import ActionProposal, ActionType, RecoveryCase
from revguard.execution import ActionAdapter, AdapterResult, MockAdapter

logger = logging.getLogger(__name__)

# Razorpay caps ``reference_id`` (Payment Links) and ``receipt`` (Orders) at 40 characters.
_MAX_PROVIDER_REF = 40


def _to_paise(amount: Decimal) -> int:
    """Convert a major-unit Decimal amount to integer paise (Razorpay's smallest unit)."""
    return int((amount * 100).to_integral_value())


def _idempotency_key(case: RecoveryCase, proposal: ActionProposal) -> str:
    supplied = proposal.parameters.get("idempotency_key")
    if isinstance(supplied, str) and supplied:
        return supplied
    return f"{case.case_id}:{proposal.action_type.value}:{case.attempt_count}"


def _provider_ref(key: str) -> str:
    """A deterministic, ≤40-char provider reference derived from the idempotency key.

    Razorpay limits ``reference_id`` / ``receipt`` to 40 chars, but our case ids are longer.
    Short keys pass through unchanged (traceability); longer ones collapse to a stable hash so
    the same logical action always maps to the same provider reference (idempotency preserved).
    The full ``case_id`` is always carried in ``notes`` for reconciliation, unaffected by this.
    """
    if len(key) <= _MAX_PROVIDER_REF:
        return key
    return "rg_" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:24]


def _amount(case: RecoveryCase, proposal: ActionProposal) -> Decimal:
    return proposal.expected_recovery_amount or case.amount_at_risk


class RazorpayTestAdapter(ActionAdapter):
    """Adapter that performs payment actions against a Razorpay Test Mode client."""

    name = "razorpay_test"

    def __init__(self, client: Any, *, fallback: ActionAdapter | None = None) -> None:
        self._client = client
        self._fallback = fallback or MockAdapter()

    # -- Razorpay-backed payment actions ------------------------------------------------

    def retry_payment(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult:
        key = _idempotency_key(case, proposal)
        payload = {
            "amount": _to_paise(_amount(case, proposal)),
            "currency": case.currency.value,
            "receipt": _provider_ref(key),  # ≤40 chars; deterministic for idempotency
            "notes": {"case_id": case.case_id, "action": ActionType.RETRY_PAYMENT.value},
        }
        return self._call(
            ActionType.RETRY_PAYMENT,
            lambda: self._client.order.create(data=payload),
            detail="razorpay test order created for retry",
        )

    def create_payment_link(
        self, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult:
        """Create a real Razorpay Test Mode Payment Link for the case's bounded amount.

        The link's ``short_url`` is captured so an operator can see (and share) the exact
        customer-facing collection link. A successful create is still only a *technical*
        outcome — recovery is confirmed solely by the verified ``paid`` status later.
        """
        key = _idempotency_key(case, proposal)
        payload = {
            "amount": _to_paise(_amount(case, proposal)),
            "currency": case.currency.value,
            "reference_id": _provider_ref(key),  # ≤40 chars; deterministic for idempotency
            "description": f"RevGuard recovery for case {case.case_id}",
            # ``notes.case_id`` is what webhook reconciliation matches the payment back to.
            "notes": {"case_id": case.case_id, "action": ActionType.CREATE_PAYMENT_LINK.value},
        }
        return self._call(
            ActionType.CREATE_PAYMENT_LINK,
            lambda: self._client.payment_link.create(payload),
            detail="razorpay test payment link created",
        )

    # -- non-payment actions: not Razorpay operations → delegate to the fallback --------

    def send_reminder(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult:
        return self._fallback.send_reminder(case, proposal)

    def record_promise_to_pay(
        self, case: RecoveryCase, proposal: ActionProposal
    ) -> AdapterResult:
        return self._fallback.record_promise_to_pay(case, proposal)

    def wait(self, case: RecoveryCase, proposal: ActionProposal) -> AdapterResult:
        return self._fallback.wait(case, proposal)

    # -- helpers ------------------------------------------------------------------------

    def _call(
        self, action: ActionType, api_call, *, detail: str
    ) -> AdapterResult:
        """Run a Razorpay API call and map it to a technical AdapterResult (never recovery)."""
        try:
            response = api_call()
        except Exception as exc:  # noqa: BLE001 - map any SDK/HTTP error to a technical fail
            # Log by TYPE only; never include the exception message, request, or credentials.
            logger.warning("razorpay %s call failed: %s", action.value, type(exc).__name__)
            return AdapterResult(
                action=action,
                accepted=False,
                succeeded=False,
                detail=f"razorpay {action.value} call failed",
                failure_reason=type(exc).__name__,
                simulated=False,
            )

        return AdapterResult(
            action=action,
            accepted=True,
            succeeded=True,
            detail=detail,
            reference=_reference(response),
            url=_short_url(response),
            simulated=False,  # a real Test Mode API call — but NOT a recovery claim
        )


def _field(response: Any, key: str) -> Any:
    """Read ``key`` from a Razorpay response (dict- or object-shaped)."""
    if isinstance(response, dict):
        return response.get(key)
    return getattr(response, key, None)


def _reference(response: Any) -> str | None:
    """Extract the provider object id from a Razorpay response (dict- or object-shaped)."""
    rid = _field(response, "id")
    return str(rid) if rid is not None else None


def _short_url(response: Any) -> str | None:
    """Extract a customer-facing ``short_url`` when present (Payment Links)."""
    url = _field(response, "short_url")
    return str(url) if url else None


__all__ = ["RazorpayTestAdapter"]
