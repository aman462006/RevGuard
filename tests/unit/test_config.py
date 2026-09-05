"""Phase 0: configuration loads with sane defaults and honors env overrides."""

from __future__ import annotations

from revguard.config import Settings, get_settings


def test_defaults():
    # Assert the code defaults, independent of any local .env file a developer may have.
    settings = Settings(_env_file=None)
    assert settings.app_name == "RevGuard"
    assert settings.environment == "development"
    assert settings.ai_provider == "mock"  # offline-first default
    assert settings.database_url.startswith("sqlite")


def test_env_override(monkeypatch):
    monkeypatch.setenv("REVGUARD_AI_PROVIDER", "anthropic")
    monkeypatch.setenv("REVGUARD_ENVIRONMENT", "test")
    settings = Settings()
    assert settings.ai_provider == "anthropic"
    assert settings.environment == "test"


def test_get_settings_returns_settings():
    assert isinstance(get_settings(), Settings)
