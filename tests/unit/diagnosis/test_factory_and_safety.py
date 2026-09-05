"""Factory/offline behaviour, provider mockability, and the AI-safety boundary."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import revguard.diagnosis as diagnosis_pkg
from revguard.config import Settings
from revguard.diagnosis import (
    AnthropicProvider,
    Diagnoser,
    GeminiProvider,
    MockDiagnoser,
    create_diagnoser,
)
from revguard.domain import ActionProposal, ActionType, DecisionType, WorkflowType
from revguard.policy import PolicyEngine


def test_default_settings_use_mock_offline():
    diag = create_diagnoser(Settings(ai_provider="mock"))
    assert isinstance(diag, MockDiagnoser)


def test_anthropic_without_key_falls_back_to_mock():
    diag = create_diagnoser(Settings(ai_provider="anthropic", anthropic_api_key=None))
    assert isinstance(diag, MockDiagnoser)


def test_anthropic_with_key_returns_anthropic_provider():
    diag = create_diagnoser(
        Settings(ai_provider="anthropic", anthropic_api_key="sk-test-not-real")
    )
    assert isinstance(diag, AnthropicProvider)  # constructed lazily; no client/API call


def test_gemini_without_key_falls_back_to_mock():
    diag = create_diagnoser(Settings(ai_provider="gemini", gemini_api_key=None))
    assert isinstance(diag, MockDiagnoser)


def test_gemini_with_key_returns_gemini_provider():
    diag = create_diagnoser(
        Settings(ai_provider="gemini", gemini_api_key="gemini-test-key")
    )
    assert isinstance(diag, GeminiProvider)


def test_provider_interface_can_be_mocked(make_case):
    class StubDiagnoser(Diagnoser):
        name = "stub"

        def diagnose(self, case):
            return ActionProposal(
                case_id=case.case_id,
                action_type=ActionType.WAIT,
                rationale="stubbed",
                confidence=0.5,
            )

    diag: Diagnoser = StubDiagnoser()
    proposal = diag.diagnose(make_case())
    assert proposal.action_type is ActionType.WAIT


def test_proposal_has_no_execution_authority(make_case):
    # A proposal cannot represent approval/execution: no such fields exist.
    proposal = MockDiagnoser().diagnose(make_case())
    fields = set(type(proposal).model_fields)
    assert "approved" not in fields
    assert "executed" not in fields
    assert not hasattr(proposal, "execute")


def test_ai_cannot_bypass_policy_engine(make_case):
    # The AI proposes a concrete recovery action...
    case = make_case(WorkflowType.FAILED_SUBSCRIPTION, amount=Decimal("90000.00"))
    proposal = MockDiagnoser().diagnose(case)
    assert proposal.action_type is ActionType.RETRY_PAYMENT

    # ...but the deterministic engine still gates it (amount over threshold → ESCALATE).
    decision = PolicyEngine().evaluate(proposal, case)
    assert decision.decision is DecisionType.ESCALATE


def test_diagnosis_layer_has_no_side_effect_imports():
    """AGENT_SPEC: the diagnosis module imports only domain models + config, never the
    executor, DB, policy engine, audit, or payment SDK."""
    forbidden = (
        "revguard.execution",
        "revguard.persistence",
        "revguard.integrations",
        "revguard.policy",
        "revguard.audit",
        "revguard.orchestrator",
        "import razorpay",
        "from razorpay",
    )
    pkg_dir = Path(diagnosis_pkg.__file__).parent
    for path in pkg_dir.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path.name} must not reference {token!r}"
