"""CLI dispatch: default health-check plus the Phase 8 ``evaluate`` sub-command."""

from __future__ import annotations

import json

from revguard.__main__ import main


def test_no_command_runs_health_check(capsys):
    assert main([]) == 0
    assert '"status": "ok"' in capsys.readouterr().out


def test_evaluate_prints_human_table(capsys):
    assert main(["evaluate", "--seed", "7"]) == 0
    out = capsys.readouterr().out
    assert "RevGuard evaluation - seed 7" in out
    assert "baseline" in out and "revguard" in out


def test_evaluate_json_is_machine_readable(capsys):
    assert main(["evaluate", "--json", "--seed", "7"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["seed"] == 7
    assert {"baseline", "revguard", "delta"} <= set(payload["comparison"])
