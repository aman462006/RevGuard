"""Incoming events — the raw inputs to the pipeline (Stage 0 → Detector).

Events come from synthetic sources now and, in a later phase, from Razorpay webhooks. The
model deliberately stays decoupled from any Razorpay SDK type: source-specific extras live
in the untyped ``metadata`` map, while the fields RevGuard reasons about are explicit and
typed. Events are immutable once created.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    FROZEN_MODEL,
    Currency,
    PositiveMoney,
    new_id,
    utcnow,
)


class EventType(StrEnum):
    """Explicit whitelist of event kinds RevGuard understands."""

    PAYMENT_FAILED = "payment_failed"
    PAYMENT_SUCCEEDED = "payment_succeeded"
    SUBSCRIPTION_PAYMENT_FAILED = "subscription_payment_failed"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    INVOICE_OVERDUE = "invoice_overdue"
    PROMISE_TO_PAY = "promise_to_pay"
    PAYMENT_DEGRADATION_OBSERVED = "payment_degradation_observed"


class EventSource(StrEnum):
    """Where an event originated. Keeps the domain agnostic of the transport."""

    SYNTHETIC = "synthetic"
    RAZORPAY_WEBHOOK = "razorpay_webhook"
    INTERNAL = "internal"


class DataProvenance(StrEnum):
    """Origin of the data behind a signal/case — for honest UI labelling only.

    Distinguishes generated demo data (which never represents real customer money or a real
    merchant) from data that originated at the real payment provider or an internal
    integration. This is a *labelling* concern: it never affects detection, policy, or
    recovery logic.
    """

    SYNTHETIC = "synthetic"  # generated demo/test data — not real customer money
    RAZORPAY = "razorpay"    # originated from a real Razorpay object (e.g. a webhook)
    INTERNAL = "internal"    # submitted to the events API by an internal integration

    @property
    def is_synthetic(self) -> bool:
        return self is DataProvenance.SYNTHETIC


# Each event source maps to the provenance it contributes. Unknown sources are treated as
# INTERNAL (real) rather than synthetic, so production data is never mislabelled as demo data.
_SOURCE_PROVENANCE: dict[EventSource, DataProvenance] = {
    EventSource.SYNTHETIC: DataProvenance.SYNTHETIC,
    EventSource.RAZORPAY_WEBHOOK: DataProvenance.RAZORPAY,
    EventSource.INTERNAL: DataProvenance.INTERNAL,
}


def provenance_from_sources(sources: Iterable[EventSource]) -> DataProvenance:
    """Resolve the provenance of a group of events by "realness" precedence.

    A group is only labelled SYNTHETIC when *every* contributing event is synthetic. Any real
    event (a Razorpay object, or an internal integration event) downgrades the label so that
    production data can never be mislabelled as synthetic demo data. An empty group defaults
    to INTERNAL (the conservative, non-synthetic choice).
    """
    mapped = {_SOURCE_PROVENANCE.get(s, DataProvenance.INTERNAL) for s in sources}
    if not mapped:
        return DataProvenance.INTERNAL
    if DataProvenance.RAZORPAY in mapped:
        return DataProvenance.RAZORPAY
    if DataProvenance.INTERNAL in mapped:
        return DataProvenance.INTERNAL
    return DataProvenance.SYNTHETIC


class Event(BaseModel):
    """A single immutable input event."""

    model_config = FROZEN_MODEL

    event_id: str = Field(default_factory=lambda: new_id("evt"))
    event_type: EventType
    source: EventSource
    occurred_at: AwareDatetime = Field(default_factory=utcnow)

    # Entity references — present "where applicable" per the event type.
    customer_id: str | None = None
    payment_id: str | None = None
    order_id: str | None = None
    subscription_id: str | None = None
    invoice_id: str | None = None

    # Monetary context — amount and currency travel together or not at all.
    amount: PositiveMoney | None = None
    currency: Currency | None = None

    # Source-specific extras (e.g. a raw Razorpay payload fragment, failure codes).
    # Intentionally open-ended so new webhook fields need no domain change.
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _amount_currency_paired(self) -> Event:
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be provided together")
        return self


__all__ = [
    "Event",
    "EventType",
    "EventSource",
    "DataProvenance",
    "provenance_from_sources",
]
