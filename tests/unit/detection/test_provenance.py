"""Data-provenance labelling: the DetectionEngine stamps each signal with the origin of the
events it was derived from, and production/real data is never mislabelled as synthetic."""

from __future__ import annotations

from decimal import Decimal

from revguard.detection.engine import DetectionEngine
from revguard.domain import (
    Currency,
    DataProvenance,
    Event,
    EventSource,
    EventType,
    provenance_from_sources,
)
from revguard.synthetic.generator import generate_events


def _resource(events: list[Event], source: EventSource) -> list[Event]:
    """Copy an event stream, overriding every event's source (events are frozen)."""
    return [e.model_copy(update={"source": source}) for e in events]


def test_provenance_from_sources_precedence():
    assert provenance_from_sources([]) is DataProvenance.INTERNAL
    assert provenance_from_sources([EventSource.SYNTHETIC]) is DataProvenance.SYNTHETIC
    # Any real event downgrades a group away from synthetic.
    assert (
        provenance_from_sources([EventSource.SYNTHETIC, EventSource.INTERNAL])
        is DataProvenance.INTERNAL
    )
    assert (
        provenance_from_sources([EventSource.SYNTHETIC, EventSource.RAZORPAY_WEBHOOK])
        is DataProvenance.RAZORPAY
    )
    assert (
        provenance_from_sources([EventSource.INTERNAL, EventSource.RAZORPAY_WEBHOOK])
        is DataProvenance.RAZORPAY
    )


def test_engine_labels_synthetic_events_as_synthetic():
    signals = DetectionEngine().run(generate_events())
    assert signals
    assert all(s.provenance is DataProvenance.SYNTHETIC for s in signals)


def test_engine_never_labels_internal_events_synthetic():
    signals = DetectionEngine().run(_resource(generate_events(), EventSource.INTERNAL))
    assert signals
    assert all(s.provenance is DataProvenance.INTERNAL for s in signals)
    assert not any(s.provenance.is_synthetic for s in signals)


def test_engine_labels_razorpay_events_as_razorpay():
    signals = DetectionEngine().run(
        _resource(generate_events(), EventSource.RAZORPAY_WEBHOOK)
    )
    assert signals
    assert all(s.provenance is DataProvenance.RAZORPAY for s in signals)


def _sub_fail(source: EventSource, attempt: int) -> Event:
    return Event(
        event_type=EventType.SUBSCRIPTION_PAYMENT_FAILED,
        source=source,
        customer_id="cust_mix",
        subscription_id="sub_mix",
        amount=Decimal("1200.00"),
        currency=Currency.INR,
        metadata={"failure_reason": "card_declined", "attempt": attempt},
    )


def test_mixed_group_with_one_real_event_is_not_synthetic():
    # Two failures for the same subscription raise one signal; one event is real (internal),
    # so the whole signal must NOT be labelled synthetic.
    events = [
        _sub_fail(EventSource.SYNTHETIC, 1),
        _sub_fail(EventSource.INTERNAL, 2),
    ]
    signals = DetectionEngine().run(events)
    assert len(signals) == 1
    assert signals[0].provenance is DataProvenance.INTERNAL
    assert signals[0].provenance.is_synthetic is False
