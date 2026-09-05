"""RevGuard detection layer (Stage 1) — deterministic revenue-risk detectors.

Each detector consumes :class:`~revguard.domain.Event` data for one subject and emits a
:class:`~revguard.domain.RevenueRiskSignal` (or ``None``). Detectors contain no LLM/API/DB
calls and never choose or execute a recovery action. :class:`DetectionEngine` routes a flat
event stream to the four workflow detectors.
"""

from revguard.detection.base import Detector
from revguard.detection.checkout_abandonment import (
    CheckoutAbandonmentConfig,
    CheckoutAbandonmentDetector,
)
from revguard.detection.engine import DetectionEngine
from revguard.detection.failed_subscription import (
    FailedSubscriptionConfig,
    FailedSubscriptionDetector,
)
from revguard.detection.overdue_receivable import (
    OverdueReceivableConfig,
    OverdueReceivableDetector,
)
from revguard.detection.payment_degradation import (
    PaymentDegradationConfig,
    PaymentDegradationDetector,
)

__all__ = [
    "Detector",
    "DetectionEngine",
    "PaymentDegradationDetector",
    "PaymentDegradationConfig",
    "FailedSubscriptionDetector",
    "FailedSubscriptionConfig",
    "CheckoutAbandonmentDetector",
    "CheckoutAbandonmentConfig",
    "OverdueReceivableDetector",
    "OverdueReceivableConfig",
]
