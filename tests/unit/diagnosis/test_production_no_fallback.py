"""Production never silently falls back from Gemini to the mock; demo explicitly uses mocks.

The live run path refuses to assemble on the mock diagnoser (it raises, surfaced as HTTP 503),
while demo mode is a clearly-labelled rehearsal wired to the deterministic mocks. Gemini stays
the production provider throughout.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from revguard.api.dependencies import get_run_agent
from revguard.config import Settings
from revguard.diagnosis import GeminiProvider, MockDiagnoser, create_diagnoser
from revguard.integrations.razorpay import IntegrationError, build_integrated_agent
from revguard.persistence import Database


@pytest.fixture
def database() -> Database:
    db = Database("sqlite:///:memory:")
    db.create_all()
    try:
        yield db
    finally:
        db.dispose()


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, mode="production", ai_provider="gemini")
    base.update(overrides)
    return Settings(**base)


# -- production: no silent fallback to the mock ----------------------------------------


def test_integrated_agent_refuses_to_fall_back_to_mock_without_gemini_key(database):
    # Production Gemini selected but no key: must RAISE, never quietly build a MockDiagnoser.
    settings = _settings(gemini_api_key=None, razorpay_key_id="rzp_test_x",
                         razorpay_key_secret="secret")
    with pytest.raises(IntegrationError):
        build_integrated_agent(database, settings)


def test_integrated_agent_uses_gemini_when_configured(database):
    settings = _settings(
        gemini_api_key="test-key", razorpay_key_id="rzp_test_x", razorpay_key_secret="secret"
    )
    # Inject a fake Razorpay client so no real payment SDK/network is touched.
    agent = build_integrated_agent(database, settings, client=SimpleNamespace())
    assert isinstance(agent._diagnoser, GeminiProvider)  # the live provider, not the mock


def test_run_dependency_returns_503_when_production_is_unconfigured(database):
    # get_run_agent in production with no credentials must fail closed as HTTP 503 — not mock.
    settings = _settings(gemini_api_key=None)
    state = SimpleNamespace(settings=settings, db=database)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    with pytest.raises(Exception) as excinfo:
        get_run_agent(request)
    assert getattr(excinfo.value, "status_code", None) == 503


# -- demo: explicit, credential-free mocks ---------------------------------------------


def test_demo_mode_uses_the_mock_diagnoser(database):
    settings = Settings(_env_file=None, mode="demo", ai_provider="gemini")
    state = SimpleNamespace(settings=settings, db=database)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    agent = get_run_agent(request)
    assert isinstance(agent._diagnoser, MockDiagnoser)


# -- create_diagnoser stays offline-first but selects Gemini when a key is present ------


def test_create_diagnoser_selects_gemini_with_a_key():
    settings = _settings(gemini_api_key="test-key")
    assert isinstance(create_diagnoser(settings), GeminiProvider)


def test_create_diagnoser_without_key_does_not_build_a_live_provider():
    # Offline-first default (used only by the non-diagnosing ingest agent): a missing key never
    # yields a live provider; the *run* path is guarded separately by build_integrated_agent.
    settings = _settings(gemini_api_key=None)
    assert isinstance(create_diagnoser(settings), MockDiagnoser)
