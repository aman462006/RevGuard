"""Minimal health-check capability.

Provides a dependency-light way to confirm the application is importable, configuration
loads, and the runtime is sane. Used by ``python -m revguard`` and by the test suite. A
richer HTTP ``/health`` endpoint is added when the FastAPI layer arrives (Phase 10); this
function is what that endpoint will delegate to.
"""

from __future__ import annotations

import platform
import sys

from pydantic import BaseModel

from revguard import __version__
from revguard.config import Settings, get_settings


class HealthStatus(BaseModel):
    """Structured result of a health check."""

    status: str
    app_name: str
    version: str
    environment: str
    ai_provider: str
    python_version: str


def check_health(settings: Settings | None = None) -> HealthStatus:
    """Run the health check and return a structured status.

    This performs no I/O and has no side effects, so it is safe to call from anywhere
    (CLI, tests, and — later — an HTTP endpoint).
    """
    settings = settings or get_settings()
    return HealthStatus(
        status="ok",
        app_name=settings.app_name,
        version=__version__,
        environment=settings.environment,
        ai_provider=settings.ai_provider,
        python_version=platform.python_version(),
    )


def _running_python_supported() -> bool:
    """Whether the current interpreter matches the project's target range.

    Informational only — never raises. The authoritative constraint lives in
    ``pyproject.toml`` (``requires-python = ">=3.12,<3.14"``).
    """
    return (3, 12) <= sys.version_info[:2] < (3, 14)
