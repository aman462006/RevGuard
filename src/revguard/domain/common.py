"""Shared domain primitives: enums, money handling, and value types.

Pure Python + Pydantic only. This module must not import any other RevGuard package
(besides being imported by the rest of ``domain``), keeping the dependency graph a DAG and
avoiding circular imports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import ConfigDict, Field
from pydantic.functional_validators import AfterValidator

# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def utcnow() -> datetime:
    """Timezone-aware current time (UTC). All domain timestamps use this."""
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------


def new_id(prefix: str) -> str:
    """Generate a unique, human-scannable identifier such as ``evt_<hex>``."""
    return f"{prefix}_{uuid4().hex}"


# ---------------------------------------------------------------------------
# Money — Decimal only, never float. Non-finite (NaN/Inf) is always rejected.
# ---------------------------------------------------------------------------


def _ensure_finite_decimal(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("monetary amount must be a finite Decimal (no NaN/Infinity)")
    return value


# Monetary amounts are modelled in *major* currency units (e.g. rupees, not paise) with up
# to two decimal places. Conversion to/from Razorpay's smallest-unit integers happens at
# the integration boundary in a later phase, never in the domain layer.
PositiveMoney = Annotated[
    Decimal,
    Field(gt=0, max_digits=18, decimal_places=2),
    AfterValidator(_ensure_finite_decimal),
]
NonNegativeMoney = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=2),
    AfterValidator(_ensure_finite_decimal),
]

# Probability-like score in the closed interval [0, 1].
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


# ---------------------------------------------------------------------------
# Controlled vocabularies (enums, never free strings)
# ---------------------------------------------------------------------------


class Currency(StrEnum):
    """Explicit ISO-4217 currencies RevGuard supports. Extend as needed."""

    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


class WorkflowType(StrEnum):
    """The four revenue-risk workflows (A–D) from PRODUCT_SPEC §3.

    Shared taxonomy used as both the signal category and the case type so the
    detection→case mapping is unambiguous.
    """

    PAYMENT_DEGRADATION = "payment_degradation"          # A
    FAILED_SUBSCRIPTION = "failed_subscription"          # B
    CHECKOUT_ABANDONMENT = "checkout_abandonment"        # C
    OVERDUE_RECEIVABLE = "overdue_receivable"            # D


class RiskLevel(StrEnum):
    """Severity of a detected revenue-at-risk signal."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Reusable strict model configs.
FROZEN_MODEL = ConfigDict(frozen=True, extra="forbid")
MUTABLE_MODEL = ConfigDict(validate_assignment=True, extra="forbid")


__all__ = [
    "utcnow",
    "new_id",
    "PositiveMoney",
    "NonNegativeMoney",
    "Confidence",
    "Currency",
    "WorkflowType",
    "RiskLevel",
    "FROZEN_MODEL",
    "MUTABLE_MODEL",
]
