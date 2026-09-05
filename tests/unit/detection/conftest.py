"""Shared event factory for detector unit tests.

Builds valid :class:`Event` objects with sensible defaults so each test can construct just
the events relevant to the rule under test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from revguard.domain import Currency, Event, EventSource, EventType

BASE = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def make_event() -> Callable[..., Event]:
    counter = {"n": 0}

    def _make(
        event_type: EventType,
        *,
        minute: int = 0,
        amount: Decimal | None = None,
        currency: Currency | None = Currency.INR,
        metadata: dict | None = None,
        **fields,
    ) -> Event:
        counter["n"] += 1
        # currency travels with amount only.
        cur = currency if amount is not None else None
        return Event(
            event_id=f"evt_test_{counter['n']:04d}",
            event_type=event_type,
            source=EventSource.SYNTHETIC,
            occurred_at=BASE + timedelta(minutes=minute),
            amount=amount,
            currency=cur,
            metadata=metadata or {},
            **fields,
        )

    return _make
