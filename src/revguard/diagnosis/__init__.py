"""RevGuard diagnosis layer (Stage 3) — the AI recommendation component.

A :class:`Diagnoser` turns a :class:`~revguard.domain.RecoveryCase` into a single
:class:`~revguard.domain.ActionProposal` (a recommendation only). :func:`create_diagnoser`
selects the provider from configuration and **always falls back to the offline
:class:`MockDiagnoser`** when Anthropic is not selected or no API key is present — so the
system runs with zero credentials by default.
"""

from __future__ import annotations

import logging

from revguard.config import Settings, get_settings
from revguard.diagnosis.anthropic_provider import AnthropicProvider
from revguard.diagnosis.diagnoser import Diagnoser, safe_fallback_proposal
from revguard.diagnosis.errors import AIFailureKind, classify_exception, user_message
from revguard.diagnosis.gemini_provider import GeminiProvider
from revguard.diagnosis.groq_provider import GroqProvider
from revguard.diagnosis.mock_diagnoser import MockDiagnoser

logger = logging.getLogger(__name__)


def create_diagnoser(settings: Settings | None = None) -> Diagnoser:
    """Return the configured diagnoser, defaulting to the offline mock.

    Falls back to :class:`MockDiagnoser` if a configured provider is requested without a
    valid API key, so missing credentials never break offline operation.
    """
    settings = settings or get_settings()
    provider = (settings.ai_provider or "mock").strip().lower()

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning(
                "ai_provider=anthropic but no ANTHROPIC_API_KEY set; using MockDiagnoser"
            )
            return MockDiagnoser()
        return AnthropicProvider(
            model=settings.ai_model,
            api_key=settings.anthropic_api_key,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_s=settings.ai_timeout_s,
        )

    if provider == "gemini":
        if not settings.gemini_api_key:
            logger.warning(
                "ai_provider=gemini but no GEMINI_API_KEY set; using MockDiagnoser"
            )
            return MockDiagnoser()
        return GeminiProvider(
            model=settings.ai_model,
            api_key=settings.gemini_api_key,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_s=settings.ai_timeout_s,
        )

    if provider == "groq":
        if not settings.groq_api_key:
            logger.warning("ai_provider=groq but no GROQ_API_KEY set; using MockDiagnoser")
            return MockDiagnoser()
        return GroqProvider(
            model=settings.ai_model,
            api_key=settings.groq_api_key,
            temperature=settings.ai_temperature,
            max_tokens=settings.ai_max_tokens,
            timeout_s=settings.ai_timeout_s,
        )

    return MockDiagnoser()


__all__ = [
    "Diagnoser",
    "MockDiagnoser",
    "AnthropicProvider",
    "GeminiProvider",
    "GroqProvider",
    "create_diagnoser",
    "safe_fallback_proposal",
    "AIFailureKind",
    "classify_exception",
    "user_message",
]
