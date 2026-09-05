"""Razorpay webhook: HMAC signature verification and mapping into domain models."""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest

from revguard.domain import Currency, EventSource, EventType
from revguard.integrations.razorpay.webhook import (
    WebhookVerificationError,
    ensure_verified,
    map_webhook_to_event,
    map_webhook_to_outcome,
    parse_webhook,
    verify_webhook_signature,
)

_SECRET = "whsec_test_123"


def _sign(payload: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _paid_body() -> dict:
    return {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {"id": "plink_1", "status": "paid", "amount": 150000,
                           "currency": "INR"}
            },
            "payment": {
                "entity": {"id": "pay_1", "status": "captured", "order_id": "order_1",
                           "amount": 150000, "currency": "INR"}
            },
        },
    }


def test_valid_signature_verifies():
    payload = b'{"event":"payment.captured"}'
    assert verify_webhook_signature(payload, _sign(payload), _SECRET) is True


def test_tampered_body_fails():
    payload = b'{"event":"payment.captured"}'
    sig = _sign(payload)
    assert verify_webhook_signature(payload + b" ", sig, _SECRET) is False


def test_wrong_secret_and_missing_signature_fail():
    payload = b'{"event":"x"}'
    assert verify_webhook_signature(payload, _sign(payload), "other") is False
    assert verify_webhook_signature(payload, "", _SECRET) is False


def test_ensure_verified_raises_on_bad_signature():
    with pytest.raises(WebhookVerificationError):
        ensure_verified(b"{}", "deadbeef", _SECRET)


def test_parse_webhook_verifies_then_parses():
    body = _paid_body()
    raw = json.dumps(body).encode()
    parsed = parse_webhook(raw, _sign(raw), _SECRET)
    assert parsed["event"] == "payment_link.paid"


def test_paid_event_maps_to_recovered_outcome():
    outcome = map_webhook_to_outcome(_paid_body())
    assert outcome.recovered is True
    assert outcome.amount == Decimal("1500.00")
    assert outcome.currency is Currency.INR
    assert outcome.payment_id == "pay_1"
    assert outcome.order_id == "order_1"
    assert outcome.payment_link_id == "plink_1"


def test_failed_event_is_not_recovered():
    body = {
        "event": "payment.failed",
        "payload": {"payment": {"entity": {"id": "pay_9", "status": "failed",
                                            "amount": 5000, "currency": "INR"}}},
    }
    outcome = map_webhook_to_outcome(body)
    assert outcome.recovered is False
    assert outcome.status == "failed"


def test_paid_event_maps_to_success_domain_event():
    event = map_webhook_to_event(_paid_body())
    assert event.event_type is EventType.PAYMENT_SUCCEEDED
    assert event.source is EventSource.RAZORPAY_WEBHOOK
    assert event.amount == Decimal("1500.00")
    assert event.currency is Currency.INR
    assert event.metadata["razorpay_event"] == "payment_link.paid"


def test_unknown_event_is_not_dropped():
    event = map_webhook_to_event({"event": "payment.authorized", "payload": {}})
    assert event.event_type is EventType.PAYMENT_DEGRADATION_OBSERVED
    assert event.amount is None and event.currency is None  # invariant: paired or neither


def test_case_reference_is_extracted_from_entity_notes():
    body = _paid_body()
    body["payload"]["payment_link"]["entity"]["notes"] = {
        "case_id": "case_failed_subscription_sub_1",
        "action": "create_payment_link",
    }
    outcome = map_webhook_to_outcome(body)
    assert outcome.case_reference == "case_failed_subscription_sub_1"


def test_case_reference_absent_when_no_notes():
    assert map_webhook_to_outcome(_paid_body()).case_reference is None
