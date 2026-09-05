"""FastAPI webhook endpoint for Razorpay payment events (Phase 9).

A thin transport layer over the framework-free core in
:mod:`revguard.integrations.razorpay.webhook`. It reads the **raw** request body (required for
a correct HMAC check), verifies the ``X-Razorpay-Signature`` header against the configured
webhook secret, and — only on success — maps the payload into our domain models. An invalid
or missing signature is rejected with HTTP 400 and nothing downstream sees the payload.

FastAPI is imported lazily inside :func:`create_app`, so importing this module never requires
the web framework; the core engine and its tests do not depend on it. Secrets are never
logged or returned in responses.
"""

# NOTE: intentionally no ``from __future__ import annotations`` — FastAPI must resolve the
# endpoint's ``Request``/``Header`` annotations at definition time, and those types are
# imported lazily inside ``create_app`` (so importing this module never requires FastAPI).

import logging
from collections.abc import Callable
from typing import Any

from revguard.config import Settings, get_settings
from revguard.domain import Event
from revguard.integrations.razorpay.webhook import (
    RazorpayPaymentOutcome,
    map_webhook_to_event,
    map_webhook_to_outcome,
    parse_webhook,
)

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Razorpay-Signature"

# A sink invoked with each verified webhook (e.g. to persist the event / drive verification).
WebhookSink = Callable[[RazorpayPaymentOutcome, Event], None]


def create_app(
    settings: Settings | None = None,
    *,
    on_event: WebhookSink | None = None,
) -> Any:
    """Build a FastAPI app exposing ``POST /webhooks/razorpay``.

    ``on_event`` (optional) receives the verified ``(outcome, event)`` for each accepted
    webhook. The webhook secret comes from ``settings.razorpay_webhook_secret``.
    """
    from fastapi import FastAPI, Header, HTTPException, Request  # lazy import

    settings = settings or get_settings()
    app = FastAPI(title="RevGuard Webhooks", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/razorpay")
    async def razorpay_webhook(
        request: Request,
        x_razorpay_signature: str | None = Header(default=None, alias=SIGNATURE_HEADER),
    ) -> dict[str, Any]:
        secret = settings.razorpay_webhook_secret
        if not secret:
            # Misconfiguration, not a client error; never reveal secret material.
            raise HTTPException(status_code=503, detail="webhook verification is not configured")

        raw = await request.body()
        try:
            body = parse_webhook(raw, x_razorpay_signature or "", secret)
        except Exception:
            # Covers signature failure and malformed JSON; do not echo the payload.
            logger.warning("rejected razorpay webhook: signature/parse failure")
            raise HTTPException(
                status_code=400, detail="invalid webhook signature or payload"
            ) from None

        outcome = map_webhook_to_outcome(body)
        event = map_webhook_to_event(body)
        if on_event is not None:
            on_event(outcome, event)

        return {
            "status": "ok",
            "event": outcome.event,
            "recovered": outcome.recovered,
            "event_type": event.event_type.value,
        }

    return app


__all__ = ["create_app", "SIGNATURE_HEADER", "WebhookSink"]
