"""FastAPI Razorpay webhook endpoint: signature-gated ingestion, mocked payloads only."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from revguard.config import Settings

# The endpoint needs FastAPI (optional extra); skip cleanly if it is not installed.
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from revguard.api import create_app  # noqa: E402

_SECRET = "whsec_test_endpoint"


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, razorpay_webhook_secret=_SECRET)
    base.update(overrides)
    return Settings(**base)


def _sign(raw: bytes, secret: str = _SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _paid_payload() -> bytes:
    body = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {"entity": {"id": "plink_1", "status": "paid",
                                        "amount": 250000, "currency": "INR"}},
            "payment": {"entity": {"id": "pay_1", "status": "captured",
                                   "order_id": "order_1", "amount": 250000,
                                   "currency": "INR"}},
        },
    }
    return json.dumps(body).encode()


def test_valid_signed_webhook_is_accepted_and_mapped():
    seen = []
    client = TestClient(create_app(_settings(), on_event=lambda o, e: seen.append((o, e))))
    raw = _paid_payload()
    resp = client.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": _sign(raw)}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["recovered"] is True
    assert data["event_type"] == "payment_succeeded"
    # The verified outcome/event reached the sink.
    assert seen and seen[0][0].payment_id == "pay_1"


def test_bad_signature_is_rejected():
    client = TestClient(create_app(_settings()))
    raw = _paid_payload()
    resp = client.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": "deadbeef"}
    )
    assert resp.status_code == 400


def test_missing_signature_is_rejected():
    client = TestClient(create_app(_settings()))
    resp = client.post("/webhooks/razorpay", content=_paid_payload())
    assert resp.status_code == 400


def test_unconfigured_secret_returns_service_unavailable():
    client = TestClient(create_app(_settings(razorpay_webhook_secret=None)))
    raw = _paid_payload()
    resp = client.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": _sign(raw)}
    )
    assert resp.status_code == 503
