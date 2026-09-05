"""RevGuard domain layer — the typed contracts between pipeline stages.

Pure Python + Pydantic. This layer has no dependency on FastAPI, SQLAlchemy, the Razorpay
SDK, the Anthropic SDK, or any database/external code.
"""

from revguard.domain.cases import (
    TERMINAL_STATUSES,
    CaseStatus,
    RecoveryCase,
    StopReason,
)
from revguard.domain.common import (
    Confidence,
    Currency,
    NonNegativeMoney,
    PositiveMoney,
    RiskLevel,
    WorkflowType,
    new_id,
    utcnow,
)
from revguard.domain.decisions import DecisionType, PolicyDecision
from revguard.domain.escalation import EscalationRecord, EscalationStatus
from revguard.domain.events import (
    DataProvenance,
    Event,
    EventSource,
    EventType,
    provenance_from_sources,
)
from revguard.domain.promise import PromiseStatus, PromiseToPay
from revguard.domain.proposals import (
    EXECUTABLE_ACTIONS,
    RECOMMENDATION_ACTIONS,
    ActionProposal,
    ActionType,
)
from revguard.domain.results import (
    ExecutionStatus,
    RecoveryOutcome,
    RecoveryResult,
    VerificationStatus,
)
from revguard.domain.signals import RevenueRiskSignal, SignalType

__all__ = [
    # common
    "utcnow",
    "new_id",
    "Currency",
    "WorkflowType",
    "RiskLevel",
    "PositiveMoney",
    "NonNegativeMoney",
    "Confidence",
    # events
    "Event",
    "EventType",
    "EventSource",
    "DataProvenance",
    "provenance_from_sources",
    # signals
    "RevenueRiskSignal",
    "SignalType",
    # cases
    "RecoveryCase",
    "CaseStatus",
    "StopReason",
    "TERMINAL_STATUSES",
    # promise-to-pay
    "PromiseToPay",
    "PromiseStatus",
    # proposals
    "ActionType",
    "ActionProposal",
    "EXECUTABLE_ACTIONS",
    "RECOMMENDATION_ACTIONS",
    # decisions
    "DecisionType",
    "PolicyDecision",
    # results
    "RecoveryResult",
    "ExecutionStatus",
    "VerificationStatus",
    "RecoveryOutcome",
    # escalation
    "EscalationRecord",
    "EscalationStatus",
]
