"""Razorpay client factory — Test Mode only, credentials from config, never hard-coded.

The official ``razorpay`` SDK is imported **lazily** (only when a real client is built), so
importing this module — and running the whole app on the offline ``MockAdapter`` — never
requires the SDK. For tests, a fake client is injected everywhere a client is consumed; no
real client is ever constructed and no network call is made.

Two guardrails are enforced here and only here:

* **Test Mode only** — a key id must look like ``rzp_test_...``. A live key (``rzp_live_...``)
  or any other shape is rejected, so the recovery engine can never touch real money.
* **No credential leakage** — errors mention the *shape* of the problem, never the secret.
"""

from __future__ import annotations

from typing import Any

from revguard.config import Settings, get_settings

_TEST_KEY_PREFIX = "rzp_test_"


class RazorpayConfigError(Exception):
    """Raised when Razorpay credentials are missing or are not Test Mode credentials."""


def _mask(key_id: str) -> str:
    """A log-safe fragment of a key id (never the secret, never the full id)."""
    return f"{key_id[:11]}..." if len(key_id) > 11 else "rzp_***"


def require_test_credentials(settings: Settings | None = None) -> tuple[str, str]:
    """Return ``(key_id, key_secret)`` after asserting they are present Test Mode keys."""
    settings = settings or get_settings()
    key_id = settings.razorpay_key_id
    key_secret = settings.razorpay_key_secret
    if not key_id or not key_secret:
        raise RazorpayConfigError(
            "Razorpay credentials are not configured; set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET (Test Mode) in the environment/.env"
        )
    if not key_id.startswith(_TEST_KEY_PREFIX):
        # Never echo the provided value; only state the requirement.
        raise RazorpayConfigError(
            "refusing to use a non-Test-Mode Razorpay key id "
            f"({_mask(key_id)}); only 'rzp_test_...' keys are allowed"
        )
    return key_id, key_secret


def create_client(settings: Settings | None = None) -> Any:
    """Build a real Razorpay Test Mode client. The SDK is imported lazily.

    Raises :class:`RazorpayConfigError` if credentials are missing or not Test Mode.
    """
    key_id, key_secret = require_test_credentials(settings)
    try:
        import razorpay  # lazy: offline mode never needs the SDK
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RazorpayConfigError(
            "the 'razorpay' package is not installed; install the optional extra "
            "(pip install 'revguard[razorpay]') to enable Razorpay Test Mode"
        ) from exc
    return razorpay.Client(auth=(key_id, key_secret))


__all__ = ["RazorpayConfigError", "create_client", "require_test_credentials"]
