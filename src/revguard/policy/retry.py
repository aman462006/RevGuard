"""Deterministic retry sequencing (POLICY_SPEC — bounded retry schedule).

A failed-but-recoverable case should be retried on a *fixed, deterministic cadence* rather
than hammered immediately or given up on. This module owns that cadence. It is pure policy
code: the AI may recommend *which* recovery action to take, but *when* a retry is eligible is
never the model's decision — it is computed here from the attempt count alone.

The schedule is a tuple of delays (seconds) applied *before* each attempt:

    attempt 1 → immediate (0s), attempt 2 → +30m, attempt 3 → +6h, attempt 4 → +24h

Once the schedule is exhausted no further retry is scheduled and the case falls to the
existing maximum-attempts STOP — retries are always bounded, never indefinite. There is no
scheduler or background job here: the schedule only computes the *next eligible time*, which
the orchestrator persists on the case so a later run (or a run after a restart) can resume it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

# Delay (seconds) BEFORE attempt 1, 2, 3, 4. Attempt 1 is immediate; the gaps are the
# "+30 minutes / +6 hours / +24 hours" retry cadence. len() bounds the number of attempts.
DEFAULT_RETRY_SCHEDULE_SECONDS: tuple[int, ...] = (0, 1800, 21600, 86400)


def retry_delay_seconds(
    attempts_made: int, schedule: tuple[int, ...] = DEFAULT_RETRY_SCHEDULE_SECONDS
) -> int | None:
    """Delay before the *next* attempt given how many attempts already completed.

    ``attempts_made`` is 0 before the first attempt, 1 after it, etc. Returns ``None`` when the
    schedule is exhausted (no further retry — the attempt budget then stops the case).
    """
    index = max(attempts_made, 0)
    if index >= len(schedule):
        return None
    return schedule[index]


def next_retry_at(
    reference: datetime,
    attempts_made: int,
    schedule: tuple[int, ...] = DEFAULT_RETRY_SCHEDULE_SECONDS,
) -> datetime | None:
    """The next eligible retry time = ``reference`` + the scheduled delay, or ``None`` if the
    schedule is exhausted."""
    delay = retry_delay_seconds(attempts_made, schedule)
    if delay is None:
        return None
    return reference + timedelta(seconds=delay)


def retries_remaining(
    attempts_made: int, schedule: tuple[int, ...] = DEFAULT_RETRY_SCHEDULE_SECONDS
) -> bool:
    """Whether the schedule still has an attempt left after ``attempts_made`` completed ones."""
    return max(attempts_made, 0) < len(schedule)


__all__ = [
    "DEFAULT_RETRY_SCHEDULE_SECONDS",
    "retry_delay_seconds",
    "next_retry_at",
    "retries_remaining",
]
