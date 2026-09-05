"""A live-provider failure fails closed to ESCALATE, is audited, and surfaces a clear reason.

Drives the real RecoveryAgent with a Gemini provider whose client raises a quota error. The
PolicyEngine still governs the outcome (ESCALATE), the executor never runs, the case carries a
concise credential-free reason, and the audit trail records the classified provider failure.
"""

from __future__ import annotations

from types import SimpleNamespace

from revguard.audit import AuditLog, AuditStage
from revguard.diagnosis.gemini_provider import GeminiProvider
from revguard.domain import CaseStatus
from revguard.execution import MockAdapter
from revguard.orchestrator import RecoveryAgent
from revguard.persistence import Database


class _QuotaError(Exception):
    code = 429

    def __str__(self) -> str:
        return "RESOURCE_EXHAUSTED: quota exceeded"


class _RaisingGeminiClient:
    def __init__(self) -> None:
        self.models = SimpleNamespace(generate_content=self._raise)

    def _raise(self, **kwargs):
        raise _QuotaError()


def test_gemini_quota_failure_escalates_and_is_audited(database: Database, make_case):
    adapter = MockAdapter()
    agent = RecoveryAgent(
        database,
        diagnoser=GeminiProvider(model="gemini-3.5-flash", client=_RaisingGeminiClient()),
        adapter=adapter,
    )
    case = make_case()

    agent.process_case(case)

    # Fail closed through the existing ESCALATE path — nothing executed.
    assert case.status is CaseStatus.ESCALATED
    assert adapter.calls == []
    # The case carries the concise, user-friendly failure reason (no raw provider text/secret).
    assert "quota" in (case.escalation_reason or "").lower()
    assert "RESOURCE_EXHAUSTED" not in (case.escalation_reason or "")

    # The classified provider failure is recorded in the audit trail.
    with database.session() as s:
        entries = AuditLog(s).for_case(case.case_id)
    diagnosis = [e for e in entries if e.stage is AuditStage.DIAGNOSIS]
    assert diagnosis and diagnosis[-1].details.get("ai_failure") is True
    assert diagnosis[-1].details.get("failure_kind") == "quota"
    assert diagnosis[-1].details.get("provider") == "gemini"
