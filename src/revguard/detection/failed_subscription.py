"""Workflow B — Failed subscription / mandate detector.

Consumes recurring-payment failure events for one subscription and, unless the payment was
subsequently recovered, emits a signal whose severity grows with the number of consecutive
failed attempts. Deterministic and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revguard.detection.base import adjust_risk, has_success_after, latest
from revguard.domain import (
    Currency,
    EventType,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


@dataclass(frozen=True)
class FailedSubscriptionConfig:
    """Severity thresholds for failed subscription/mandate recovery.

    ``high_value_amount`` bumps severity one level and is expressed in INR major units
    (the primary demo currency); it is only applied to INR-denominated risk.
    """

    high_value_amount: Decimal = Decimal("5000")


class FailedSubscriptionDetector:
    """Detects unrecovered recurring/mandate payment failures."""

    workflow = WorkflowType.FAILED_SUBSCRIPTION

    def __init__(self, config: FailedSubscriptionConfig | None = None) -> None:
        self.config = config or FailedSubscriptionConfig()

    def detect(self, events) -> RevenueRiskSignal | None:  # noqa: ANN001
        failures = [
            e for e in events
            if e.event_type == EventType.SUBSCRIPTION_PAYMENT_FAILED
        ]
        if not failures:
            return None

        last_failure = latest(failures)
        # A success at/after the last failure means the mandate recovered on its own.
        if has_success_after(
            events, last_failure, event_type=EventType.PAYMENT_SUCCEEDED
        ):
            return None

        amount = last_failure.amount
        currency = last_failure.currency
        if amount is None or currency is None:
            return None

        failure_count = len(failures)
        risk = self._severity(failure_count)
        if currency == Currency.INR and amount >= self.config.high_value_amount:
            risk = adjust_risk(risk, 1)

        reasons = [
            str(e.metadata["failure_reason"])
            for e in failures
            if e.metadata.get("failure_reason")
        ]

        return RevenueRiskSignal(
            signal_type=self.workflow,
            risk_level=risk,
            customer_id=last_failure.customer_id,
            subscription_id=last_failure.subscription_id,
            amount_at_risk=amount,
            currency=currency,
            evidence={
                "failure_count": failure_count,
                "last_failure_reason": reasons[-1] if reasons else None,
            },
            source_event_ids=[e.event_id for e in failures],
            confidence=round(min(1.0, 0.5 + 0.15 * failure_count), 4),
        )

    @staticmethod
    def _severity(failure_count: int) -> RiskLevel:
        if failure_count >= 4:
            return RiskLevel.CRITICAL
        if failure_count == 3:
            return RiskLevel.HIGH
        if failure_count == 2:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


__all__ = ["FailedSubscriptionDetector", "FailedSubscriptionConfig"]
