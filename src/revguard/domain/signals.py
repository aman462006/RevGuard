"""RevenueRiskSignal — a detector's conclusion that revenue is at risk (Stage 1 output).

A signal is evidence + classification. It deliberately carries **no action**: detectors
conclude *that* revenue is at risk, never *what to do about it*. Choosing an action is the
job of the AI (proposal) and the policy engine (decision) downstream. Signals are
immutable.
"""

from __future__ import annotations

from typing import Any

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from revguard.domain.common import (
    FROZEN_MODEL,
    Confidence,
    Currency,
    PositiveMoney,
    RiskLevel,
    WorkflowType,
    new_id,
    utcnow,
)
from revguard.domain.events import DataProvenance

# A signal's type is its workflow category; kept as an alias for readable field typing.
SignalType = WorkflowType


class RevenueRiskSignal(BaseModel):
    """Detector output: a classified, evidenced statement that money is at risk."""

    model_config = FROZEN_MODEL

    signal_id: str = Field(default_factory=lambda: new_id("sig"))
    signal_type: SignalType
    risk_level: RiskLevel

    # Entity references (customer / subject of the risk), present where applicable.
    customer_id: str | None = None
    payment_id: str | None = None
    order_id: str | None = None
    subscription_id: str | None = None
    invoice_id: str | None = None

    amount_at_risk: PositiveMoney
    currency: Currency

    # Detector-provided structured evidence (metrics, failure codes, counts, ...).
    evidence: dict[str, Any] = Field(default_factory=dict)

    detected_at: AwareDatetime = Field(default_factory=utcnow)

    # The event(s) this signal was derived from. At least one is required so every signal
    # is traceable back to its inputs.
    source_event_ids: list[str] = Field(min_length=1)

    # Where the underlying data came from. Set by the DetectionEngine from the source of the
    # events this signal was derived from. Defaults to INTERNAL (the conservative, non-synthetic
    # value) so a signal is never mislabelled synthetic when provenance is unknown.
    provenance: DataProvenance = DataProvenance.INTERNAL

    # Optional detector confidence in the [0, 1] range, where meaningful.
    confidence: Confidence | None = None

    @model_validator(mode="after")
    def _no_duplicate_source_events(self) -> RevenueRiskSignal:
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids must not contain duplicates")
        return self


__all__ = ["RevenueRiskSignal", "SignalType"]
