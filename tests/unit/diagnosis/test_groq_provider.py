"""GroqProvider (Llama 3.3) tests — valid parsing and fail-closed behaviour.

A fake OpenAI/Groq-shaped client is injected everywhere, so no network call is made and no
credential is required. The provider must return a valid proposal for good output and a safe
escalation recommendation (never an executable action) for any error or malformed output.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from revguard.diagnosis import GroqProvider
from revguard.domain import ActionType


def _client(content: str | None = None, *, error: Exception | None = None):
    """A minimal Groq-shaped client: ``client.chat.completions.create(...)``."""

    class Completions:
        def create(self, **kwargs):  # noqa: ANN003
            if error is not None:
                raise error
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


def _provider(client):
    return GroqProvider(model="llama-3.3-70b-versatile", client=client)


def test_valid_json_proposal_is_parsed(make_case):
    case = make_case()
    client = _client(
        '{"action_type": "retry_payment", "rationale": "transient decline", "confidence": 0.85}'
    )
    proposal = _provider(client).diagnose(case)
    assert proposal.action_type is ActionType.RETRY_PAYMENT
    assert proposal.confidence == 0.85
    assert proposal.case_id == case.case_id
    assert proposal.evidence.get("provider") == "groq"
    assert not proposal.evidence.get("fallback")


def test_json_wrapped_in_code_fence_is_parsed(make_case):
    case = make_case()
    client = _client(
        '```json\n{"action_type": "send_reminder", "rationale": "nudge", "confidence": 0.6}\n```'
    )
    proposal = _provider(client).diagnose(case)
    assert proposal.action_type is ActionType.SEND_REMINDER


@pytest.mark.parametrize(
    "bad",
    [
        "not json at all",
        "",
        "{oops",
        '{"action_type": "fly_to_moon", "rationale": "x", "confidence": 0.5}',
    ],
)
def test_malformed_output_fails_closed_to_escalation(make_case, bad):
    proposal = _provider(_client(bad)).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.evidence.get("fallback") is True


def test_provider_error_fails_closed_and_is_classified(make_case):
    proposal = _provider(_client(error=RuntimeError("invalid api key (401)"))).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.evidence.get("fallback") is True
    assert proposal.evidence.get("failure_kind") == "authentication"


def test_rate_limit_is_classified_as_quota(make_case):
    proposal = _provider(_client(error=RuntimeError("rate limit exceeded"))).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.evidence.get("failure_kind") == "quota"


def test_provider_name_is_groq():
    assert GroqProvider(model="x").name == "groq"
