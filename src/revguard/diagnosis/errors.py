"""Classification of AI-provider failures into clear, user-friendly categories.

The live Gemini path must fail *loudly but safely*: when the provider is unavailable, quota-
limited, unauthenticated, or times out, we classify the failure into a small, stable set of
kinds and produce a concise message that carries **no** credentials or raw provider payloads.
The classification travels with the fail-closed escalation proposal (into the audit trail and
the case's escalation reason), so an operator sees *why* the AI was unavailable without any
secret ever being exposed.
"""

from __future__ import annotations

from enum import StrEnum


class AIFailureKind(StrEnum):
    """A stable, provider-agnostic classification of an AI diagnosis failure."""

    AUTH = "authentication"  # key rejected / unauthenticated / permission denied
    QUOTA = "quota"  # quota exhausted / rate limited (HTTP 429 / RESOURCE_EXHAUSTED)
    TIMEOUT = "timeout"  # request timed out / deadline exceeded
    UNAVAILABLE = "unavailable"  # network / 5xx / other transport failure
    SDK_MISSING = "sdk_missing"  # provider SDK not installed
    BAD_RESPONSE = "bad_response"  # provider returned no usable structured proposal
    MALFORMED = "malformed"  # provider returned a proposal that failed validation


_USER_MESSAGES: dict[AIFailureKind, str] = {
    AIFailureKind.AUTH: "AI authentication failed — the AI provider API key was rejected.",
    AIFailureKind.QUOTA: "AI quota or rate limit reached — please retry shortly.",
    AIFailureKind.TIMEOUT: "AI request timed out before a recommendation was returned.",
    AIFailureKind.UNAVAILABLE: "AI provider is temporarily unavailable.",
    AIFailureKind.SDK_MISSING: "AI provider SDK is not installed.",
    AIFailureKind.BAD_RESPONSE: "AI returned no usable recommendation.",
    AIFailureKind.MALFORMED: "AI returned a malformed recommendation.",
}


def user_message(kind: AIFailureKind) -> str:
    """A concise, credential-free message describing the failure for a human operator."""
    return _USER_MESSAGES.get(kind, _USER_MESSAGES[AIFailureKind.UNAVAILABLE])


def _status_code(exc: BaseException) -> int | None:
    """Best-effort HTTP status code from a provider/SDK exception (google-genai, httpx, …)."""
    for attr in ("code", "status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


# Keyword sets checked against the (lowercased) exception text. These are deliberately broad so
# the classification is robust across SDK versions and message phrasings.
_AUTH_KEYS = (
    "unauthenticated",
    "permission_denied",
    "permission denied",
    "api key not valid",
    "invalid api key",
    "api_key_invalid",
    "unauthorized",
    "invalid authentication",
)
_QUOTA_KEYS = (
    "resource_exhausted",
    "quota",
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
)
_TIMEOUT_KEYS = ("timeout", "timed out", "deadline")
_UNAVAILABLE_KEYS = ("unavailable", "connection", "network", "temporarily")


def classify_exception(exc: BaseException) -> AIFailureKind:
    """Classify a provider exception into an :class:`AIFailureKind` (never raises)."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    status = _status_code(exc)

    if "not installed" in msg or "no module named" in msg:
        return AIFailureKind.SDK_MISSING

    # Status codes are the most reliable signal when present.
    if status == 429:
        return AIFailureKind.QUOTA
    if status in (401, 403):
        return AIFailureKind.AUTH

    if "timeout" in name or any(k in msg for k in _TIMEOUT_KEYS):
        return AIFailureKind.TIMEOUT
    if any(k in msg for k in _QUOTA_KEYS):
        return AIFailureKind.QUOTA
    if any(k in msg for k in _AUTH_KEYS):
        return AIFailureKind.AUTH
    if status in (500, 502, 503, 504) or any(k in msg for k in _UNAVAILABLE_KEYS):
        return AIFailureKind.UNAVAILABLE
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return (
            AIFailureKind.TIMEOUT
            if isinstance(exc, TimeoutError)
            else AIFailureKind.UNAVAILABLE
        )
    return AIFailureKind.UNAVAILABLE


__all__ = ["AIFailureKind", "classify_exception", "user_message"]
