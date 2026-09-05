"""Deterministic retry schedule: cadence calculation + PolicyEngine ownership of timing.

These assert the schedule itself (immediate / +30m / +6h / +24h), that it is exhausted after
the bounded number of attempts, and that the PolicyEngine — not the AI — computes the next
eligible retry time from the attempt count alone.
"""

from __future__ import annotations

from datetime import timedelta

from revguard.domain import utcnow
from revguard.policy import (
    DEFAULT_RETRY_SCHEDULE_SECONDS,
    PolicyConfig,
    PolicyEngine,
    next_retry_at,
    retries_remaining,
    retry_delay_seconds,
)


def test_schedule_is_immediate_then_30m_6h_24h():
    assert DEFAULT_RETRY_SCHEDULE_SECONDS == (0, 1800, 21600, 86400)
    assert retry_delay_seconds(0) == 0  # attempt 1 immediate
    assert retry_delay_seconds(1) == 1800  # +30 minutes
    assert retry_delay_seconds(2) == 21600  # +6 hours
    assert retry_delay_seconds(3) == 86400  # +24 hours


def test_schedule_is_exhausted_after_the_last_slot():
    # After the final scheduled attempt there is no further retry — the case must stop instead.
    assert retry_delay_seconds(4) is None
    assert retries_remaining(3) is True
    assert retries_remaining(4) is False
    assert next_retry_at(utcnow(), 4) is None


def test_next_retry_at_adds_the_scheduled_delay():
    now = utcnow()
    assert next_retry_at(now, 1) == now + timedelta(minutes=30)
    assert next_retry_at(now, 2) == now + timedelta(hours=6)
    assert next_retry_at(now, 3) == now + timedelta(hours=24)


def test_policy_engine_owns_retry_timing_from_attempt_count(make_case):
    now = utcnow()
    engine = PolicyEngine()
    # attempt_count is the number of attempts already made; the engine derives the next slot.
    assert engine.next_retry_at(make_case(attempt_count=1), now) == now + timedelta(minutes=30)
    assert engine.next_retry_at(make_case(attempt_count=3), now) == now + timedelta(hours=24)
    assert engine.next_retry_at(make_case(attempt_count=4), now) is None
    assert engine.has_retries_remaining(make_case(attempt_count=3)) is True
    assert engine.has_retries_remaining(make_case(attempt_count=4)) is False


def test_schedule_length_matches_the_attempt_budget():
    # The bounded schedule and the max-attempts stop stay in step, so retries never outrun the
    # attempt budget nor stop short of it.
    assert len(DEFAULT_RETRY_SCHEDULE_SECONDS) == PolicyConfig().max_attempts
