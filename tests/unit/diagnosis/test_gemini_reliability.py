"""Gemini reliability: classify provider failures and fail closed to a safe escalation.

When Gemini is unavailable, quota-limited, unauthenticated, or times out, the provider must
NOT fall back to the mock or fabricate a proposal — it returns a classified, credential-free
escalation recommendation that the deterministic PolicyEngine routes to a human.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from revguard.diagnosis.errors import AIFailureKind, classify_exception, user_message
from revguard.diagnosis.gemini_provider import GeminiProvider
from revguard.domain import ActionType


class _FakeGeminiClient:
    """Stand-in google-genai client: returns a canned response or raises a provider error."""

    def __init__(self, *, response=None, error=None):
        self._response = response
        self._error = error
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        if self._error is not None:
            raise self._error
        return self._response


def _json_response(text: str):
    return SimpleNamespace(
        candidates=[
            SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text=text)]))
        ]
    )


# A provider/SDK error carrying an HTTP-style status code (like google-genai's APIError).
class _ApiError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _diagnose_with_error(make_case, error):
    client = _FakeGeminiClient(error=error)
    return GeminiProvider(model="gemini-3.5-flash", client=client).diagnose(make_case())


# -- success ---------------------------------------------------------------------------


def test_gemini_success_returns_the_recommended_action(make_case):
    client = _FakeGeminiClient(
        response=_json_response(
            '{"action_type": "retry_payment", "rationale": "mandate failed once", '
            '"confidence": 0.8}'
        )
    )
    proposal = GeminiProvider(model="gemini-3.5-flash", client=client).diagnose(make_case())
    assert proposal.action_type is ActionType.RETRY_PAYMENT
    assert proposal.confidence == 0.8
    assert not proposal.evidence.get("fallback")


# -- classified failures fail closed to escalation -------------------------------------


@pytest.mark.parametrize(
    "error, kind",
    [
        (_ApiError(429, "RESOURCE_EXHAUSTED: quota exceeded for this model"), AIFailureKind.QUOTA),
        (_ApiError(401, "API key not valid. Please pass a valid API key."), AIFailureKind.AUTH),
        (_ApiError(403, "PERMISSION_DENIED"), AIFailureKind.AUTH),
        (TimeoutError("deadline exceeded"), AIFailureKind.TIMEOUT),
        (_ApiError(503, "The service is currently unavailable"), AIFailureKind.UNAVAILABLE),
        (RuntimeError("something unexpected"), AIFailureKind.UNAVAILABLE),
    ],
)
def test_provider_failures_fail_closed_with_classification(make_case, error, kind):
    proposal = _diagnose_with_error(make_case, error)

    # Never executes: always a safe escalation recommendation, confidence 0.
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.confidence == 0.0
    # Classified, and the reason is the user-friendly (credential-free) message.
    assert proposal.evidence.get("fallback") is True
    assert proposal.evidence.get("failure_kind") == kind.value
    assert proposal.rationale == user_message(kind)


def test_quota_failure_message_mentions_quota(make_case):
    proposal = _diagnose_with_error(make_case, _ApiError(429, "rate limit"))
    assert "quota" in proposal.rationale.lower() or "rate limit" in proposal.rationale.lower()


def test_failure_reason_never_leaks_the_raw_provider_message(make_case):
    secret = "AIzaSy-SUPER-SECRET-KEY-should-never-appear"
    proposal = _diagnose_with_error(make_case, _ApiError(401, f"API key not valid: {secret}"))
    assert secret not in proposal.rationale
    assert proposal.evidence.get("failure_kind") == AIFailureKind.AUTH.value


# -- empty / malformed model output ----------------------------------------------------


def test_empty_model_output_is_a_bad_response_escalation(make_case):
    client = _FakeGeminiClient(response=_json_response(""))  # no JSON object
    proposal = GeminiProvider(model="gemini-3.5-flash", client=client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.evidence.get("failure_kind") == AIFailureKind.BAD_RESPONSE.value


def test_malformed_json_is_a_malformed_escalation(make_case):
    client = _FakeGeminiClient(response=_json_response('{"action_type": "retry_payment"}'))
    proposal = GeminiProvider(model="gemini-3.5-flash", client=client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.evidence.get("failure_kind") == AIFailureKind.MALFORMED.value


# -- classifier unit coverage ----------------------------------------------------------


def test_classify_exception_covers_the_documented_kinds():
    assert classify_exception(_ApiError(429, "x")) is AIFailureKind.QUOTA
    assert classify_exception(_ApiError(401, "x")) is AIFailureKind.AUTH
    assert classify_exception(Exception("Request timed out")) is AIFailureKind.TIMEOUT
    assert classify_exception(Exception("RESOURCE_EXHAUSTED")) is AIFailureKind.QUOTA
    assert classify_exception(Exception("UNAUTHENTICATED")) is AIFailureKind.AUTH
    assert classify_exception(RuntimeError("google-genai SDK is not installed")) is (
        AIFailureKind.SDK_MISSING
    )
    assert classify_exception(ConnectionError("boom")) is AIFailureKind.UNAVAILABLE
