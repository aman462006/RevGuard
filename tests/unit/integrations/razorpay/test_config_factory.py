"""Config + factories: Test Mode enforcement, adapter selection, integrated wiring."""

from __future__ import annotations

import pytest

from revguard.config import Settings
from revguard.diagnosis import AnthropicProvider
from revguard.execution import MockAdapter
from revguard.integrations.razorpay import (
    IntegrationError,
    RazorpayConfigError,
    RazorpayTestAdapter,
    RazorpayVerifier,
    build_integrated_agent,
    create_payment_adapter,
    require_test_credentials,
)
from revguard.persistence import Database


def _settings(**overrides) -> Settings:
    # Hermetic: ignore any local .env so assertions are about the passed values only.
    base = dict(_env_file=None)
    base.update(overrides)
    return Settings(**base)


def test_missing_credentials_are_rejected():
    with pytest.raises(RazorpayConfigError):
        require_test_credentials(_settings())


def test_live_mode_key_is_rejected():
    settings = _settings(razorpay_key_id="rzp_live_abc", razorpay_key_secret="secret")
    with pytest.raises(RazorpayConfigError):
        require_test_credentials(settings)


def test_test_mode_key_is_accepted():
    settings = _settings(razorpay_key_id="rzp_test_abc", razorpay_key_secret="secret")
    assert require_test_credentials(settings) == ("rzp_test_abc", "secret")


def test_payment_adapter_defaults_to_mock():
    assert isinstance(create_payment_adapter(_settings()), MockAdapter)


def test_payment_adapter_razorpay_with_injected_client():
    settings = _settings(
        payment_adapter="razorpay",
        razorpay_key_id="rzp_test_abc",
        razorpay_key_secret="secret",
    )
    adapter = create_payment_adapter(settings, client=object())
    assert isinstance(adapter, RazorpayTestAdapter)


def test_integrated_agent_requires_anthropic_not_mock():
    # Mock AI provider must be refused for the real/demo path.
    settings = _settings(
        ai_provider="mock",
        razorpay_key_id="rzp_test_abc",
        razorpay_key_secret="secret",
    )
    db = Database("sqlite:///:memory:")
    with pytest.raises(IntegrationError):
        build_integrated_agent(db, settings)


def test_integrated_agent_wires_anthropic_and_razorpay():
    settings = _settings(
        ai_provider="anthropic",
        anthropic_api_key="sk-ant-test",  # not used: no network, client injected
        razorpay_key_id="rzp_test_abc",
        razorpay_key_secret="secret",
    )
    db = Database("sqlite:///:memory:")
    agent = build_integrated_agent(db, settings, client=object())
    # Requirement #7: the integrated path reasons with Anthropic, never the mock diagnoser.
    assert isinstance(agent._diagnoser, AnthropicProvider)
    assert isinstance(agent._adapter, RazorpayTestAdapter)
    assert isinstance(agent._verifier, RazorpayVerifier)
