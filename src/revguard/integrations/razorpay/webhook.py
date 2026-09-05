"""Razorpay webhook handling: signature verification + mapping to domain models (Phase 9).

Two responsibilities, both framework-free (pure stdlib) so they are independently testable:

1. **Signature verification** — Razorpay signs the *raw* request body with the webhook secret
   using HMAC-SHA256. :func:`verify_webhook_signature` recomputes it and compares in constant
   time. An unsigned or tampered body is rejected; nothing downstream trusts an unverified
   payload. (This mirrors the SDK's ``utility.verify_webhook_signature`` without needing it.)

2. **Mapping** — a verified payment webhook is translated into our existing domain models:
   a :class:`~revguard.domain.Event` (``source=RAZORPAY_WEBHOOK``) plus a compact
   :class:`RazorpayPaymentOutcome`. The verified provider ``status`` — not the mere arrival of
   a webhook — is what marks an outcome as recovered, so this stays the source of truth.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from revguard.domain import Currency, Event, EventSource, EventType

# Razorpay event names that report a *successful* collection (money in) vs a failure.
_PAID_EVENTS: frozenset[str] = frozenset(
    {"payment.captured", "payment_link.paid", "order.paid", "subscription.charged"}
)
_FAILED_EVENTS: frozenset[str] = frozenset({"payment.failed", "payment_link.cancelled"})


class WebhookVerificationError(Exception):
    """Raised when a webhook body fails HMAC signature verification."""


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Return True iff ``signature`` is the HMAC-SHA256 of ``payload`` under ``secret``.

    ``payload`` must be the exact raw request bytes. Comparison is constant-time.
    """
    if not signature or not secret:
        return False
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def ensure_verified(payload: bytes, signature: str, secret: str) -> None:
    """Raise :class:`WebhookVerificationError` unless the signature verifies."""
    if not verify_webhook_signature(payload, signature, secret):
        raise WebhookVerificationError("razorpay webhook signature verification failed")


@dataclass(frozen=True)
class RazorpayPaymentOutcome:
    """A normalised view of a payment webhook, decoupled from the raw Razorpay shape."""

    event: str
    recovered: bool
    status: str | None
    amount: Decimal | None
    currency: Currency | None
    payment_id: str | None
    order_id: str | None
    payment_link_id: str | None
    invoice_id: str | None
    subscription_id: str | None
    # The RevGuard case id echoed back in the entity ``notes`` (the executor stamps
    # ``notes.case_id`` on every order/link it creates). This is the primary, unambiguous key
    # for reconciling a webhook to the case that initiated the recovery.
    case_reference: str | None = None


def _entity(event_body: dict[str, Any], key: str) -> dict[str, Any]:
    """Pull ``payload.<key>.entity`` from a Razorpay webhook body (empty dict if absent)."""
    payload = event_body.get("payload") or {}
    section = payload.get(key) or {}
    entity = section.get("entity") or {}
    return entity if isinstance(entity, dict) else {}


def _amount_from_paise(entity: dict[str, Any]) -> Decimal | None:
    raw = entity.get("amount")
    if raw is None:
        return None
    return (Decimal(int(raw)) / Decimal(100)).quantize(Decimal("0.01"))


def _currency(entity: dict[str, Any]) -> Currency | None:
    code = entity.get("currency")
    if not code:
        return None
    try:
        return Currency(code)
    except ValueError:
        return None


def _case_reference(event_body: dict[str, Any]) -> str | None:
    """Find ``notes.case_id`` on any entity in the webhook body (the executor stamps it)."""
    for key in ("payment", "order", "payment_link", "invoice", "subscription"):
        notes = _entity(event_body, key).get("notes")
        if isinstance(notes, dict):
            case_id = notes.get("case_id")
            if isinstance(case_id, str) and case_id:
                return case_id
    return None


def map_webhook_to_outcome(event_body: dict[str, Any]) -> RazorpayPaymentOutcome:
    """Map a parsed Razorpay webhook body to a :class:`RazorpayPaymentOutcome`.

    ``recovered`` is True only for a recognised *paid* event — never for a failure and never
    merely because a webhook was received.
    """
    event_name = str(event_body.get("event", ""))
    payment = _entity(event_body, "payment")
    order = _entity(event_body, "order")
    link = _entity(event_body, "payment_link")
    invoice = _entity(event_body, "invoice")
    subscription = _entity(event_body, "subscription")

    # Prefer the entity that actually carries the money fields.
    money_entity = payment or link or order or invoice or {}

    return RazorpayPaymentOutcome(
        event=event_name,
        recovered=event_name in _PAID_EVENTS,
        status=money_entity.get("status"),
        amount=_amount_from_paise(money_entity),
        currency=_currency(money_entity),
        payment_id=payment.get("id"),
        order_id=order.get("id") or payment.get("order_id"),
        payment_link_id=link.get("id"),
        invoice_id=invoice.get("id"),
        subscription_id=subscription.get("id"),
        case_reference=_case_reference(event_body),
    )


def map_webhook_to_event(event_body: dict[str, Any]) -> Event:
    """Map a parsed Razorpay webhook body to a domain :class:`Event`.

    Paid events become ``PAYMENT_SUCCEEDED``; failures become ``PAYMENT_FAILED``; anything
    else is recorded as ``PAYMENT_DEGRADATION_OBSERVED`` so no webhook is silently dropped.
    Provider ids and the event name are preserved in ``metadata`` for traceability.
    """
    outcome = map_webhook_to_outcome(event_body)
    if outcome.event in _PAID_EVENTS:
        event_type = EventType.PAYMENT_SUCCEEDED
    elif outcome.event in _FAILED_EVENTS:
        event_type = EventType.PAYMENT_FAILED
    else:
        event_type = EventType.PAYMENT_DEGRADATION_OBSERVED

    # amount/currency must travel together or not at all (domain invariant).
    amount = outcome.amount if outcome.currency is not None else None
    currency = outcome.currency if amount is not None else None

    return Event(
        event_type=event_type,
        source=EventSource.RAZORPAY_WEBHOOK,
        payment_id=outcome.payment_id,
        order_id=outcome.order_id,
        subscription_id=outcome.subscription_id,
        invoice_id=outcome.invoice_id,
        amount=amount,
        currency=currency,
        metadata={
            "razorpay_event": outcome.event,
            "status": outcome.status,
            "payment_link_id": outcome.payment_link_id,
        },
    )


def parse_webhook(payload: bytes, signature: str, secret: str) -> dict[str, Any]:
    """Verify the signature, then parse the JSON body. Raises on either failure."""
    ensure_verified(payload, signature, secret)
    return json.loads(payload.decode("utf-8"))


__all__ = [
    "WebhookVerificationError",
    "RazorpayPaymentOutcome",
    "verify_webhook_signature",
    "ensure_verified",
    "map_webhook_to_outcome",
    "map_webhook_to_event",
    "parse_webhook",
]
