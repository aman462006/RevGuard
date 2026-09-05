"""Event model: valid creation, enum enforcement, money + currency rules, tz-awareness."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from revguard.domain import Currency, Event, EventSource, EventType


def test_valid_event(valid_event: Event):
    assert valid_event.event_type is EventType.SUBSCRIPTION_PAYMENT_FAILED
    assert valid_event.amount == Decimal("1500.00")
    assert valid_event.currency is Currency.INR
    assert valid_event.event_id.startswith("evt_")


def test_unique_event_ids():
    e1 = Event(event_type=EventType.PAYMENT_SUCCEEDED, source=EventSource.SYNTHETIC)
    e2 = Event(event_type=EventType.PAYMENT_SUCCEEDED, source=EventSource.SYNTHETIC)
    assert e1.event_id != e2.event_id


def test_invalid_event_type_rejected():
    with pytest.raises(ValidationError):
        Event(event_type="not_a_real_type", source=EventSource.SYNTHETIC)


def test_invalid_source_rejected():
    with pytest.raises(ValidationError):
        Event(event_type=EventType.PAYMENT_FAILED, source="carrier_pigeon")


def test_negative_amount_rejected():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            amount=Decimal("-5.00"),
            currency=Currency.INR,
        )


def test_infinite_amount_rejected():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            amount=Decimal("Infinity"),
            currency=Currency.INR,
        )


def test_nan_amount_rejected():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            amount=Decimal("NaN"),
            currency=Currency.INR,
        )


def test_amount_requires_currency():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            amount=Decimal("10.00"),
        )


def test_currency_requires_amount():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            currency=Currency.INR,
        )


def test_invalid_currency_rejected():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            amount=Decimal("10.00"),
            currency="XYZ",
        )


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            occurred_at=datetime(2026, 1, 1, 12, 0, 0),  # naive
        )


def test_default_timestamp_is_aware(valid_event: Event):
    assert valid_event.occurred_at.tzinfo is not None


def test_event_is_frozen(valid_event: Event):
    with pytest.raises(ValidationError):
        valid_event.customer_id = "someone_else"


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_FAILED,
            source=EventSource.SYNTHETIC,
            arbitrary_url="http://evil.example/exfiltrate",
        )


def test_roundtrip_serialization(valid_event: Event):
    restored = Event.model_validate_json(valid_event.model_dump_json())
    assert restored == valid_event
    assert restored.occurred_at.tzinfo is not None
    # Money survives as Decimal, not float.
    assert isinstance(restored.amount, Decimal)


def test_utc_timestamp_roundtrip():
    ts = datetime(2026, 8, 31, 10, 30, tzinfo=UTC)
    e = Event(event_type=EventType.PAYMENT_SUCCEEDED, source=EventSource.SYNTHETIC, occurred_at=ts)
    assert Event.model_validate_json(e.model_dump_json()).occurred_at == ts
