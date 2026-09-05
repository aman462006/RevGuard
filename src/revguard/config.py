"""Application configuration.

Typed, environment-driven settings loaded via pydantic-settings. All variables use the
``REVGUARD_`` prefix and can be provided via the process environment or a local ``.env``
file. Phase 0 keeps this intentionally minimal; later phases extend it (AI model params,
Razorpay keys, etc.) without changing how it is consumed.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default browser origins allowed to call the demo API (the Vite dev server). Overridable
# via REVGUARD_CORS_ALLOW_ORIGINS (JSON list). Only used to let the local dashboard connect;
# it grants no access to secrets, which are never returned by any endpoint.
_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class Settings(BaseSettings):
    """Runtime configuration for RevGuard."""

    model_config = SettingsConfigDict(
        env_prefix="REVGUARD_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application identity
    app_name: str = "RevGuard"
    environment: str = "development"

    # Runtime mode. "production" runs the strict live path (the configured AI provider —
    # Gemini by default — plus Razorpay Test Mode);
    # "demo" is a clearly-labelled rehearsal path that uses the offline mocks. The mode is
    # never inferred or silently changed — it is surfaced via GET /status.
    mode: str = "production"

    # AI provider selection (offline-first). Default `mock` needs no credentials.
    # Production uses `groq` (Llama 3.3); `gemini` and `anthropic` remain supported.
    ai_provider: str = "mock"

    # AI provider tuning (Phase 5). REVGUARD_AI_MODEL / REVGUARD_AI_TEMPERATURE /
    # REVGUARD_AI_MAX_TOKENS / REVGUARD_AI_TIMEOUT_S.
    ai_model: str = "llama-3.3-70b-versatile"
    ai_temperature: float = 0.0
    ai_max_tokens: int = 1024
    ai_timeout_s: float = 30.0

    # Anthropic credential. Read from the standard ANTHROPIC_API_KEY (or the prefixed
    # form). Never hard-coded; absence keeps the app on MockDiagnoser.
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ANTHROPIC_API_KEY", "REVGUARD_ANTHROPIC_API_KEY"
        ),
    )

    # Gemini credential. Read from the standard GEMINI_API_KEY (or the prefixed form).
    # Never hard-coded; absence keeps the app on MockDiagnoser.
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "REVGUARD_GEMINI_API_KEY"),
    )

    # Groq credential (Llama 3.3 — the production default). Read from the standard GROQ_API_KEY
    # (or the prefixed form). Never hard-coded; absence keeps the app on MockDiagnoser.
    groq_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("GROQ_API_KEY", "REVGUARD_GROQ_API_KEY"),
    )

    # Payment execution adapter selection (offline-first). Default `mock` needs no
    # credentials; set to `razorpay` to route approved actions to Razorpay Test Mode.
    payment_adapter: str = "mock"

    # Razorpay Test Mode credentials. Never hard-coded — read from the standard
    # RAZORPAY_* variables (or the prefixed form). Absence keeps the app on MockAdapter.
    # Only Test Mode keys (``rzp_test_...``) are accepted by the client factory.
    razorpay_key_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("RAZORPAY_KEY_ID", "REVGUARD_RAZORPAY_KEY_ID"),
    )
    razorpay_key_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RAZORPAY_KEY_SECRET", "REVGUARD_RAZORPAY_KEY_SECRET"
        ),
    )
    razorpay_webhook_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "RAZORPAY_WEBHOOK_SECRET", "REVGUARD_RAZORPAY_WEBHOOK_SECRET"
        ),
    )

    # Persistence.
    database_url: str = "sqlite:///./revguard.db"

    # CORS origins permitted to call the demo API from a browser (the dashboard dev server).
    cors_allow_origins: list[str] = Field(default_factory=lambda: list(_DEFAULT_CORS_ORIGINS))


    @property
    def is_demo(self) -> bool:
        """True only when explicitly running the rehearsal (mock) path."""
        return (self.mode or "production").strip().lower() == "demo"


def get_settings() -> Settings:
    """Return application settings loaded from the environment / ``.env``."""
    return Settings()
