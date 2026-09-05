"""AnthropicProvider with a mocked client — no real API calls, fail-closed behaviour."""

from __future__ import annotations

from revguard.diagnosis import safe_fallback_proposal
from revguard.diagnosis.anthropic_provider import AnthropicProvider
from revguard.domain import ActionProposal, ActionType
from tests.unit.diagnosis.conftest import FakeAnthropicClient, FakeMessage


def _provider(client) -> AnthropicProvider:
    return AnthropicProvider(model="claude-sonnet-4-6", client=client)


def test_valid_tool_output_becomes_action_proposal(make_case, tool_use_response):
    case = make_case()
    client = FakeAnthropicClient(
        response=tool_use_response(
            {
                "action_type": "retry_payment",
                "rationale": "mandate failed once; a retry is likely to succeed",
                "confidence": 0.7,
            }
        )
    )
    proposal = _provider(client).diagnose(case)

    assert isinstance(proposal, ActionProposal)
    assert proposal.action_type is ActionType.RETRY_PAYMENT
    assert proposal.confidence == 0.7
    # case_id is server-controlled, taken from the case, not the model.
    assert proposal.case_id == case.case_id


def test_case_id_is_not_taken_from_model(make_case, tool_use_response):
    case = make_case()
    client = FakeAnthropicClient(
        response=tool_use_response(
            {
                "action_type": "send_reminder",
                "rationale": "gentle nudge",
                "confidence": 0.6,
                # Even if the model tried to smuggle a case_id, it is ignored.
                "case_id": "case_ATTACKER",
            }
        )
    )
    proposal = _provider(client).diagnose(case)
    assert proposal.case_id == case.case_id


def test_invalid_action_type_falls_back_to_escalation(make_case, tool_use_response):
    client = FakeAnthropicClient(
        response=tool_use_response(
            {
                "action_type": "delete_database",  # not in the enum
                "rationale": "malicious",
                "confidence": 0.9,
            }
        )
    )
    proposal = _provider(client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.evidence.get("fallback") is True


def test_missing_required_field_falls_back(make_case, tool_use_response):
    client = FakeAnthropicClient(
        response=tool_use_response({"action_type": "retry_payment"})  # no rationale/conf
    )
    proposal = _provider(client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION


def test_nested_parameters_are_rejected(make_case, tool_use_response):
    client = FakeAnthropicClient(
        response=tool_use_response(
            {
                "action_type": "retry_payment",
                "rationale": "ok",
                "confidence": 0.5,
                "parameters": {"nested": {"not": "scalar"}},  # violates scalar-only
            }
        )
    )
    proposal = _provider(client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION


def test_no_tool_use_block_falls_back(make_case):
    client = FakeAnthropicClient(response=FakeMessage(content=[]))
    proposal = _provider(client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION


def test_provider_error_falls_back_safely(make_case):
    client = FakeAnthropicClient(error=RuntimeError("network exploded"))
    proposal = _provider(client).diagnose(make_case())
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.confidence == 0.0


def test_provider_sends_schema_constrained_tool_request(make_case, tool_use_response):
    client = FakeAnthropicClient(
        response=tool_use_response(
            {"action_type": "wait", "rationale": "hold", "confidence": 0.5}
        )
    )
    _provider(client).diagnose(make_case())
    sent = client.calls[0]
    assert sent["tool_choice"]["type"] == "tool"
    assert sent["tools"][0]["name"] == "submit_action_proposal"
    # System prompt constrains the model; no secrets in it.
    assert "recommend" in sent["system"].lower()


def test_fallback_proposal_never_executes(make_case):
    proposal = safe_fallback_proposal(make_case(), reason="test")
    # Fallback is a recommendation, never an executable action.
    from revguard.domain import EXECUTABLE_ACTIONS

    assert proposal.action_type not in EXECUTABLE_ACTIONS


def test_construction_does_not_require_sdk(make_case, tool_use_response):
    # A provider built with an injected client must never touch the real anthropic SDK.
    client = FakeAnthropicClient(
        response=tool_use_response(
            {"action_type": "wait", "rationale": "hold", "confidence": 0.5}
        )
    )
    provider = AnthropicProvider(model="m", client=client)
    assert provider.diagnose(make_case()).action_type is ActionType.WAIT
    assert client.calls  # the injected client was used, not a real one
