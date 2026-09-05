"""PromiseToPay — the bounded lifecycle of a promise-to-pay commitment (Workflow D).

Recording a promise is **not** recovery. A promise is a customer's commitment to pay by a
date; this value object tracks that commitment through a clear, bounded lifecycle:

    PROMISED  → recorded, before the promised time
    PENDING   → the promised time has arrived; the actual payment state is being verified
    KEPT      → a verified payment confirmed the promise (the case then becomes RECOVERED)
    MISSED    → the promised time passed without a verified payment (→ escalate/stop)

It is an immutable Pydantic value object (like a signal): the orchestrator advances it by
replacing it with a copy carrying the new status, so an invalid in-place mutation is not
representable. Money is only ever *recovered* by the verifier — a promise never asserts it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, Field

from revguard.domain.common import (
    FROZEN_MODEL,
    Currency,
    NonNegativeMoney,
    utcnow,
)


class PromiseStatus(StrEnum):
    """Lifecycle states of a promise-to-pay."""

    PROMISED = "promised"  # recorded; before the promised time
    PENDING = "pending"  # promised time reached; verifying the actual payment
    KEPT = "kept"  # a verified payment confirmed the promise
    MISSED = "missed"  # promised time passed without a verified payment

    @property
    def is_open(self) -> bool:
        """Whether the promise is still active (not yet kept or missed)."""
        return self in (PromiseStatus.PROMISED, PromiseStatus.PENDING)


class PromiseToPay(BaseModel):
    """An immutable record of a promise-to-pay and its current lifecycle state."""

    model_config = FROZEN_MODEL

    case_id: str  # the case this promise belongs to (persisted reference)
    promised_at: AwareDatetime  # when the customer promised to pay
    recorded_at: AwareDatetime = Field(default_factory=utcnow)
    status: PromiseStatus = PromiseStatus.PROMISED

    amount: NonNegativeMoney | None = None
    currency: Currency | None = None

    # Provider reference of the *recording* action (traceability), and — once a promise is
    # kept — the verified payment reference that confirmed it. A recording reference is never
    # a recovery claim.
    reference: str | None = None
    verification_reference: str | None = None

    resolved_at: AwareDatetime | None = None  # set when the promise becomes KEPT or MISSED

    @property
    def is_open(self) -> bool:
        return self.status.is_open


__all__ = ["PromiseToPay", "PromiseStatus"]
