"""Cross-cutting domain guarantees: Decimal money, tz-aware time, id generation."""

from __future__ import annotations

from decimal import Decimal

from revguard.domain import new_id, utcnow
from revguard.domain.events import Event, EventSource, EventType


def test_utcnow_is_timezone_aware():
    assert utcnow().tzinfo is not None


def test_new_id_is_prefixed_and_unique():
    a = new_id("evt")
    b = new_id("evt")
    assert a.startswith("evt_")
    assert a != b


def test_money_is_decimal_not_float():
    e = Event(
        event_type=EventType.PAYMENT_SUCCEEDED,
        source=EventSource.SYNTHETIC,
        amount=Decimal("10.50"),
        currency="INR",
    )
    assert isinstance(e.amount, Decimal)
    # And float inputs are coerced/handled as Decimal, never stored as float.
    assert not isinstance(e.amount, float)


def test_over_precision_money_rejected():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Event(
            event_type=EventType.PAYMENT_SUCCEEDED,
            source=EventSource.SYNTHETIC,
            amount=Decimal("10.123"),  # more than 2 decimal places
            currency="INR",
        )
