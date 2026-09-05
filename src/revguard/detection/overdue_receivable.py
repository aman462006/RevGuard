"""Workflow D — B2B overdue receivables / promise-to-pay detector.

Consumes overdue-invoice events (and any promise-to-pay events) for one invoice. Unless the
invoice was paid, it emits a signal whose severity scales with days overdue, then adjusts
for an outstanding promise-to-pay: an active (future-dated) promise lowers severity, while a
broken (past-due) promise raises it. Deterministic and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from revguard.detection.base import adjust_risk, has_success_after, latest
from revguard.domain import (
    Event,
    EventType,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)


@dataclass(frozen=True)
class OverdueReceivableConfig:
    """Days-overdue thresholds classifying receivable severity."""

    medium_days: int = 30
    high_days: int = 60
    critical_days: int = 90


class OverdueReceivableDetector:
    """Detects unpaid overdue invoices, accounting for promise-to-pay state."""

    workflow = WorkflowType.OVERDUE_RECEIVABLE

    def __init__(self, config: OverdueReceivableConfig | None = None) -> None:
        self.config = config or OverdueReceivableConfig()

    def detect(self, events) -> RevenueRiskSignal | None:  # noqa: ANN001
        overdues = [e for e in events if e.event_type == EventType.INVOICE_OVERDUE]
        if not overdues:
            return None

        last_overdue = latest(overdues)
        # A payment at/after the latest overdue notice means the invoice was settled.
        if has_success_after(
            events, last_overdue, event_type=EventType.PAYMENT_SUCCEEDED
        ):
            return None

        amount = last_overdue.amount
        currency = last_overdue.currency
        if amount is None or currency is None:
            return None

        days_overdue = self._days_overdue(last_overdue)
        if days_overdue is None or days_overdue < 1:
            return None  # not actually overdue yet

        risk = self._severity(days_overdue)

        promises = [e for e in events if e.event_type == EventType.PROMISE_TO_PAY]
        has_active, has_broken = self._promise_state(promises, last_overdue.occurred_at)
        if has_active:
            risk = adjust_risk(risk, -1)
        elif has_broken:
            risk = adjust_risk(risk, 1)

        return RevenueRiskSignal(
            signal_type=self.workflow,
            risk_level=risk,
            customer_id=last_overdue.customer_id,
            invoice_id=last_overdue.invoice_id,
            amount_at_risk=amount,
            currency=currency,
            evidence={
                "days_overdue": days_overdue,
                "has_active_promise": has_active,
                "has_broken_promise": has_broken,
            },
            source_event_ids=[e.event_id for e in overdues + promises],
            confidence=round(min(1.0, 0.5 + days_overdue / 180), 4),
        )

    def _severity(self, days_overdue: int) -> RiskLevel:
        cfg = self.config
        if days_overdue >= cfg.critical_days:
            return RiskLevel.CRITICAL
        if days_overdue >= cfg.high_days:
            return RiskLevel.HIGH
        if days_overdue >= cfg.medium_days:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    @staticmethod
    def _days_overdue(event: Event) -> int | None:
        raw = event.metadata.get("days_overdue")
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _promise_state(
        promises: list[Event], reference: datetime
    ) -> tuple[bool, bool]:
        """Classify promises as active (future-dated) or broken (past-due)."""
        has_active = False
        has_broken = False
        for p in promises:
            raw = p.metadata.get("promised_date")
            if not raw:
                continue
            try:
                promised = datetime.fromisoformat(str(raw))
            except ValueError:
                continue
            if promised.tzinfo is None:
                continue  # ignore ambiguous naive dates
            if promised >= reference:
                has_active = True
            else:
                has_broken = True
        return has_active, has_broken


__all__ = ["OverdueReceivableDetector", "OverdueReceivableConfig"]
