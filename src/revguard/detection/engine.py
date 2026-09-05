"""Detection engine — routes raw events to the four deterministic detectors (Stage 1).

Pure detection glue: it groups a flat event stream by the relevant subject key for each
workflow and runs the matching detector, returning the resulting signals. It performs no
diagnosis, policy, execution, or orchestration — it only turns events into signals.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence

from revguard.detection.checkout_abandonment import CheckoutAbandonmentDetector
from revguard.detection.failed_subscription import FailedSubscriptionDetector
from revguard.detection.overdue_receivable import OverdueReceivableDetector
from revguard.detection.payment_degradation import PaymentDegradationDetector
from revguard.domain import (
    Event,
    EventType,
    RevenueRiskSignal,
    provenance_from_sources,
)


def _group(
    events: Sequence[Event], key: Callable[[Event], str | None]
) -> dict[str, list[Event]]:
    """Group events by a subject key, dropping events whose key is falsy."""
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        k = key(event)
        if k:
            grouped[k].append(event)
    return dict(grouped)


class DetectionEngine:
    """Runs all four detectors over an event stream and collects their signals."""

    def __init__(
        self,
        *,
        payment_degradation: PaymentDegradationDetector | None = None,
        failed_subscription: FailedSubscriptionDetector | None = None,
        checkout_abandonment: CheckoutAbandonmentDetector | None = None,
        overdue_receivable: OverdueReceivableDetector | None = None,
    ) -> None:
        self.payment_degradation = payment_degradation or PaymentDegradationDetector()
        self.failed_subscription = failed_subscription or FailedSubscriptionDetector()
        self.checkout_abandonment = (
            checkout_abandonment or CheckoutAbandonmentDetector()
        )
        self.overdue_receivable = overdue_receivable or OverdueReceivableDetector()

    def run(self, events: Sequence[Event]) -> list[RevenueRiskSignal]:
        signals: list[RevenueRiskSignal] = []
        signals.extend(self._run_payment_degradation(events))
        signals.extend(self._run_failed_subscription(events))
        signals.extend(self._run_checkout_abandonment(events))
        signals.extend(self._run_overdue_receivable(events))
        return signals

    # -- Per-workflow grouping + dispatch ------------------------------------------------

    def _run_payment_degradation(
        self, events: Sequence[Event]
    ) -> list[RevenueRiskSignal]:
        # Degradation is assessed per payment method; only method-tagged payment attempts
        # participate, which keeps subscription/checkout/invoice payments out of it.
        payments = [
            e for e in events
            if e.event_type in (EventType.PAYMENT_FAILED, EventType.PAYMENT_SUCCEEDED)
            and e.metadata.get("method")
        ]
        groups = _group(payments, lambda e: str(e.metadata.get("method")))
        return self._collect(self.payment_degradation, groups)

    def _run_failed_subscription(
        self, events: Sequence[Event]
    ) -> list[RevenueRiskSignal]:
        relevant = [
            e for e in events
            if e.event_type == EventType.SUBSCRIPTION_PAYMENT_FAILED
            or (e.event_type == EventType.PAYMENT_SUCCEEDED and e.subscription_id)
        ]
        groups = _group(relevant, lambda e: e.subscription_id)
        return self._collect(self.failed_subscription, groups)

    def _run_checkout_abandonment(
        self, events: Sequence[Event]
    ) -> list[RevenueRiskSignal]:
        relevant = [
            e for e in events
            if e.event_type == EventType.CHECKOUT_ABANDONED
            or (e.event_type == EventType.PAYMENT_SUCCEEDED and e.order_id)
        ]
        groups = _group(relevant, lambda e: e.order_id)
        return self._collect(self.checkout_abandonment, groups)

    def _run_overdue_receivable(
        self, events: Sequence[Event]
    ) -> list[RevenueRiskSignal]:
        relevant = [
            e for e in events
            if e.event_type in (EventType.INVOICE_OVERDUE, EventType.PROMISE_TO_PAY)
            or (e.event_type == EventType.PAYMENT_SUCCEEDED and e.invoice_id)
        ]
        groups = _group(relevant, lambda e: e.invoice_id)
        return self._collect(self.overdue_receivable, groups)

    @staticmethod
    def _collect(detector, groups: dict[str, list[Event]]) -> list[RevenueRiskSignal]:
        signals = []
        for group in groups.values():
            signal = detector.detect(group)
            if signal is not None:
                signals.append(_stamp_provenance(signal, group))
        return signals


def _stamp_provenance(
    signal: RevenueRiskSignal, group: list[Event]
) -> RevenueRiskSignal:
    """Label a signal with the provenance of the events it was derived from.

    Detectors are provenance-agnostic pure functions, so the engine stamps the label here — the
    single choke point every signal passes through. It uses only the events the detector actually
    cited (``source_event_ids``), falling back to the whole group if none match.
    """
    by_id = {e.event_id: e.source for e in group}
    sources = [by_id[i] for i in signal.source_event_ids if i in by_id]
    if not sources:
        sources = [e.source for e in group]
    return signal.model_copy(update={"provenance": provenance_from_sources(sources)})


__all__ = ["DetectionEngine"]
