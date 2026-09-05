"""Workflow C — Checkout abandonment detector.

Consumes checkout-abandonment events for one order and, unless the order was later
completed, emits a signal whose severity scales with cart value (with a small bump for
repeat abandonment). Carts below a materiality floor produce no signal. Deterministic.
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
class CheckoutAbandonmentConfig:
    """Cart-value thresholds (INR major units, the primary demo currency).

    ``min_amount`` is a materiality floor below which recovery is not worth pursuing;
    the medium/high thresholds classify severity. Thresholds apply to INR risk only.
    """

    min_amount: Decimal = Decimal("100")
    medium_amount: Decimal = Decimal("2000")
    high_amount: Decimal = Decimal("10000")
    repeat_abandon_bump_at: int = 3


class CheckoutAbandonmentDetector:
    """Detects abandoned, uncompleted checkouts worth recovering."""

    workflow = WorkflowType.CHECKOUT_ABANDONMENT

    def __init__(self, config: CheckoutAbandonmentConfig | None = None) -> None:
        self.config = config or CheckoutAbandonmentConfig()

    def detect(self, events) -> RevenueRiskSignal | None:  # noqa: ANN001
        abandonments = [
            e for e in events if e.event_type == EventType.CHECKOUT_ABANDONED
        ]
        if not abandonments:
            return None

        last_abandon = latest(abandonments)
        # A success at/after the last abandonment means the customer completed checkout.
        if has_success_after(
            events, last_abandon, event_type=EventType.PAYMENT_SUCCEEDED
        ):
            return None

        amount = last_abandon.amount
        currency = last_abandon.currency
        if amount is None or currency is None:
            return None

        cfg = self.config
        # Materiality floor (INR): tiny carts are not worth a recovery attempt.
        if currency == Currency.INR and amount < cfg.min_amount:
            return None

        abandon_count = len(abandonments)
        risk = self._severity(amount, currency, cfg)
        if abandon_count >= cfg.repeat_abandon_bump_at:
            risk = adjust_risk(risk, 1)

        return RevenueRiskSignal(
            signal_type=self.workflow,
            risk_level=risk,
            customer_id=last_abandon.customer_id,
            order_id=last_abandon.order_id,
            amount_at_risk=amount,
            currency=currency,
            evidence={
                "abandon_count": abandon_count,
                "abandonment_stage": last_abandon.metadata.get("abandonment_stage"),
            },
            source_event_ids=[e.event_id for e in abandonments],
            confidence=round(min(1.0, 0.6 + 0.1 * (abandon_count - 1)), 4),
        )

    @staticmethod
    def _severity(
        amount: Decimal, currency: Currency, cfg: CheckoutAbandonmentConfig
    ) -> RiskLevel:
        # Amount-tier severity is defined for INR; other currencies default to LOW until
        # per-currency thresholds are introduced.
        if currency != Currency.INR:
            return RiskLevel.LOW
        if amount >= cfg.high_amount:
            return RiskLevel.HIGH
        if amount >= cfg.medium_amount:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW


__all__ = ["CheckoutAbandonmentDetector", "CheckoutAbandonmentConfig"]
