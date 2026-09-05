"""RevGuard execution layer (Stage 5) — performs actions only behind an APPROVE.

The executor is the single point that turns a policy ``APPROVE`` into a real action, via a
whitelist-only :class:`ActionAdapter`. It never calls an LLM, never runs arbitrary code, and
never claims recovered money — a technically successful action leaves recovery PENDING for
verification (Phase 7). Idempotency prevents the same keyed action from running twice.
"""

from revguard.execution.adapter import ADAPTER_ACTIONS, ActionAdapter, AdapterResult
from revguard.execution.errors import (
    ExecutionError,
    ExecutionRejected,
    UnsupportedActionError,
)
from revguard.execution.executor import ActionExecutor, AuditSink, ExecutionRecord
from revguard.execution.mock_adapter import MockAdapter

__all__ = [
    "ActionAdapter",
    "AdapterResult",
    "ADAPTER_ACTIONS",
    "MockAdapter",
    "ActionExecutor",
    "ExecutionRecord",
    "AuditSink",
    "ExecutionError",
    "ExecutionRejected",
    "UnsupportedActionError",
]
