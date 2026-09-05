"""GeminiProvider uses the official google-genai SDK without weakening fail-closed safety."""

from __future__ import annotations

from types import SimpleNamespace

from revguard.diagnosis.gemini_provider import GeminiProvider
from revguard.domain import ActionProposal, ActionType


class FakeGeminiClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []
        self.models = SimpleNamespace(generate_content=self.generate_content)

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._response


def test_valid_json_response_becomes_action_proposal(make_case):
    case = make_case()
    client = FakeGeminiClient(
        response=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                text=(
                                    '{"action_type": "retry_payment", '
                                    '"rationale": "mandate failed once; retry is likely", '
                                    '"confidence": 0.7}'
                                )
                            )
                        ]
                    )
                )
            ]
        )
    )

    proposal = GeminiProvider(model="gemini-2.0-flash", client=client).diagnose(case)

    assert isinstance(proposal, ActionProposal)
    assert proposal.action_type is ActionType.RETRY_PAYMENT
    assert proposal.case_id == case.case_id
    assert proposal.confidence == 0.7


def test_malformed_json_falls_back_to_escalation(make_case):
    client = FakeGeminiClient(
        response=SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[SimpleNamespace(text='{"action_type": "retry_payment"}')]
                    )
                )
            ]
        )
    )
    proposal = GeminiProvider(model="gemini-2.0-flash", client=client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.confidence == 0.0
