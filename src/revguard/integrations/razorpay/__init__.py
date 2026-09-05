"""RevGuard ↔ Razorpay Test Mode integration (Phase 9).

Exposes the Razorpay Test Mode adapter, webhook signature verification + mapping, the
status-backed verifier, and factories that assemble the **integrated** recovery agent. The
integrated path deliberately uses the real configured AI diagnoser — Gemini by default (never
the mock) — and Razorpay Test Mode for execution + verification, while leaving the PolicyEngine
safety gate and the orchestrator untouched.
"""

from __future__ import annotations

from typing import Any

from revguard.config import Settings, get_settings
from revguard.execution import ActionAdapter, MockAdapter
from revguard.integrations.razorpay.adapter import RazorpayTestAdapter
from revguard.integrations.razorpay.client import (
    RazorpayConfigError,
    create_client,
    require_test_credentials,
)
from revguard.integrations.razorpay.verifier import RazorpayVerifier
from revguard.integrations.razorpay.webhook import (
    RazorpayPaymentOutcome,
    WebhookVerificationError,
    ensure_verified,
    map_webhook_to_event,
    map_webhook_to_outcome,
    parse_webhook,
    verify_webhook_signature,
)


class IntegrationError(Exception):
    """Raised when the integrated (non-mock) path is misconfigured."""


def create_payment_adapter(
    settings: Settings | None = None, *, client: Any | None = None
) -> ActionAdapter:
    """Return the configured execution adapter.

    ``payment_adapter=razorpay`` yields a :class:`RazorpayTestAdapter` (Test Mode only);
    anything else keeps the offline :class:`~revguard.execution.MockAdapter`. A client may be
    injected (tests); otherwise a real Test Mode client is built from credentials.
    """
    settings = settings or get_settings()
    if (settings.payment_adapter or "mock").strip().lower() != "razorpay":
        return MockAdapter()
    client = client or create_client(settings)
    return RazorpayTestAdapter(client)


def build_integrated_agent(
    database: Any,
    settings: Settings | None = None,
    *,
    client: Any | None = None,
):
    """Assemble the integrated RecoveryAgent: configured AI + Razorpay Test Mode.

    The real/demo path must reason with a real AI provider, so this refuses to run on the
    mock diagnoser: ``ai_provider`` must be ``groq``, ``gemini``, or ``anthropic`` with the
    matching API key present. Razorpay credentials must be Test Mode. The PolicyEngine gate is
    unchanged.
    """
    # Imported here to avoid a module-import cycle (orchestrator imports execution/diagnosis).
    from revguard.diagnosis import (
        AnthropicProvider,
        GeminiProvider,
        GroqProvider,
        create_diagnoser,
    )
    from revguard.orchestrator import RecoveryAgent

    settings = settings or get_settings()

    provider = (settings.ai_provider or "").strip().lower()
    key_present = (
        (provider == "groq" and bool(settings.groq_api_key))
        or (provider == "gemini" and bool(settings.gemini_api_key))
        or (provider == "anthropic" and bool(settings.anthropic_api_key))
    )
    if not key_present or provider not in {"anthropic", "gemini", "groq"}:
        raise IntegrationError(
            "the integrated path requires a live AI provider (set REVGUARD_AI_PROVIDER to "
            "groq, gemini, or anthropic with the matching API key); it must not fall back to "
            "the mock diagnoser"
        )
    diagnoser = create_diagnoser(settings)
    if not isinstance(diagnoser, (AnthropicProvider, GeminiProvider, GroqProvider)):
        raise IntegrationError("expected a live diagnoser for the integrated path")

    require_test_credentials(settings)  # fail fast on missing/non-Test-Mode keys
    client = client or create_client(settings)
    adapter = RazorpayTestAdapter(client)
    verifier = RazorpayVerifier(client)
    return RecoveryAgent(database, diagnoser=diagnoser, adapter=adapter, verifier=verifier)


__all__ = [
    "RazorpayTestAdapter",
    "RazorpayVerifier",
    "RazorpayConfigError",
    "IntegrationError",
    "create_client",
    "require_test_credentials",
    "create_payment_adapter",
    "build_integrated_agent",
    "RazorpayPaymentOutcome",
    "WebhookVerificationError",
    "verify_webhook_signature",
    "ensure_verified",
    "parse_webhook",
    "map_webhook_to_outcome",
    "map_webhook_to_event",
]
