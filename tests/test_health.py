"""Phase 0: health-check capability and console entry point."""

from __future__ import annotations

from revguard.config import Settings
from revguard.health import HealthStatus, check_health


def test_check_health_ok():
    # Use hermetic settings so the check does not depend on a local .env override.
    status = check_health(Settings(_env_file=None))
    assert isinstance(status, HealthStatus)
    assert status.status == "ok"
    assert status.app_name == "RevGuard"
    assert status.version
    assert status.ai_provider == "mock"
    assert status.python_version


def test_main_returns_zero(capsys):
    from revguard.__main__ import main

    exit_code = main([])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert '"status": "ok"' in out
