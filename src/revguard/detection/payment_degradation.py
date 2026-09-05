"""Workflow A — Payment degradation detector.

Consumes a window of payment attempt events (``PAYMENT_FAILED`` / ``PAYMENT_SUCCEEDED``)
for one payment method and flags an abnormal rise in the failure rate over a baseline.

Beyond "failures went up", it segments the failures by **payment method**, **issuer/bank**,
and **error/failure code** (wherever that data is present on the events), identifies the
*dominant* degradation segment and its contribution, and emits a concise, deterministic
root-cause summary plus a bounded mitigation hint. Dimensions with no data are explicitly
marked unavailable — nothing is invented. All of this is *evidence* for the diagnosis layer:
Gemini still writes the explanation and recommends the action; the PolicyEngine still gates it.

Deterministic: given the same window it always produces the same signal.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from revguard.detection.base import sole_currency
from revguard.domain import (
    Event,
    EventType,
    RevenueRiskSignal,
    RiskLevel,
    WorkflowType,
)

_PAYMENT_TYPES = (EventType.PAYMENT_FAILED, EventType.PAYMENT_SUCCEEDED)

# Metadata keys we accept for each root-cause dimension (first present wins). Open-ended so a
# new webhook field needs no domain change; absent dimensions are reported as unavailable.
_METHOD_KEYS = ("method",)
_ISSUER_KEYS = ("issuer", "bank", "issuer_bank")
_ERROR_KEYS = ("error_code", "error_reason", "failure_code", "failure_reason")


@dataclass(frozen=True)
class PaymentDegradationConfig:
    """Thresholds for flagging payment-performance degradation.

    ``degradation_delta`` is the *absolute* increase in failure rate over baseline needed
    to raise a signal; the medium/high/critical deltas classify severity.
    """

    baseline_failure_rate: float = 0.10
    min_sample_size: int = 20
    degradation_delta: float = 0.15
    high_delta: float = 0.30
    critical_delta: float = 0.45


class PaymentDegradationDetector:
    """Detects abnormal payment failure-rate degradation for a payment method."""

    workflow = WorkflowType.PAYMENT_DEGRADATION

    def __init__(self, config: PaymentDegradationConfig | None = None) -> None:
        self.config = config or PaymentDegradationConfig()

    def detect(self, events) -> RevenueRiskSignal | None:  # noqa: ANN001
        cfg = self.config
        payments = [e for e in events if e.event_type in _PAYMENT_TYPES]
        total = len(payments)
        if total < cfg.min_sample_size:
            return None  # not enough data to conclude degradation

        failures = [e for e in payments if e.event_type == EventType.PAYMENT_FAILED]
        failure_rate = len(failures) / total
        delta = failure_rate - cfg.baseline_failure_rate
        if delta < cfg.degradation_delta:
            return None  # within the normal band

        currency = sole_currency(failures)
        if currency is None:
            return None

        amount_at_risk = sum(
            (e.amount for e in failures if e.amount is not None), Decimal("0")
        )
        if amount_at_risk <= 0:
            return None

        method = self._sole_method(failures)
        risk = self._severity(delta, cfg)
        confidence = round(min(1.0, total / (cfg.min_sample_size * 5)), 4)
        root_cause = self._root_cause(failures, method, failure_rate)

        evidence = {
            "method": method,
            "sample_size": total,
            "failure_count": len(failures),
            "observed_failure_rate": round(failure_rate, 4),
            "baseline_failure_rate": cfg.baseline_failure_rate,
            "degradation_delta": round(delta, 4),
            # Structured breakdown (consumed by Case Detail + the diagnosis briefing).
            "root_cause": root_cause,
            # Flattened scalars so the diagnosis prompt (which forwards scalar evidence)
            # sees the dominant segment without needing the nested object.
            "root_cause_summary": root_cause["summary"],
            "suggested_mitigation": root_cause["suggested_mitigation"],
            "primary_dimension": root_cause["primary"]["dimension"],
            "primary_segment": root_cause["primary"]["segment"],
            "primary_segment_share": root_cause["primary"]["share"],
            "issuer_data_available": root_cause["dimensions"]["issuer"]["available"],
            "error_code_data_available": root_cause["dimensions"]["error_code"]["available"],
        }

        return RevenueRiskSignal(
            signal_type=self.workflow,
            risk_level=risk,
            customer_id=None,  # degradation is method-wide, not customer-specific
            amount_at_risk=amount_at_risk,
            currency=currency,
            evidence=evidence,
            source_event_ids=[e.event_id for e in payments],
            confidence=confidence,
        )

    # -- root-cause segmentation --------------------------------------------------------

    def _root_cause(
        self, failures: list[Event], method: str, failure_rate: float
    ) -> dict:
        """Segment failures by method / issuer / error code and pick the dominant driver."""
        dimensions = {
            "method": _breakdown(failures, _METHOD_KEYS),
            "issuer": _breakdown(failures, _ISSUER_KEYS),
            "error_code": _breakdown(failures, _ERROR_KEYS),
        }
        primary = _primary_dimension(dimensions)
        summary = _summary(method, failure_rate, dimensions, primary)
        mitigation = _mitigation(primary)
        return {
            "affected_method": method,
            "total_failures": len(failures),
            "dimensions": dimensions,
            "primary": primary,
            "summary": summary,
            "suggested_mitigation": mitigation,
        }

    @staticmethod
    def _severity(delta: float, cfg: PaymentDegradationConfig) -> RiskLevel:
        if delta >= cfg.critical_delta:
            return RiskLevel.CRITICAL
        if delta >= cfg.high_delta:
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    @staticmethod
    def _sole_method(events: list[Event]) -> str:
        methods = {
            e.metadata.get("method") for e in events if e.metadata.get("method")
        }
        if len(methods) == 1:
            return str(next(iter(methods)))
        return "mixed"


def _first_value(event: Event, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = event.metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _breakdown(failures: list[Event], keys: tuple[str, ...]) -> dict:
    """Failure count + amount per segment value for one dimension.

    Returns ``{"available": False}`` when no failure carries this dimension (never invented),
    else the per-segment counts/amounts, the dominant segment, and its share of failures.
    """
    seg_failures: dict[str, int] = {}
    seg_amount: dict[str, Decimal] = {}
    present = 0
    for event in failures:
        value = _first_value(event, keys)
        if value is None:
            continue
        present += 1
        seg_failures[value] = seg_failures.get(value, 0) + 1
        seg_amount[value] = seg_amount.get(value, Decimal("0")) + (event.amount or Decimal("0"))

    if present == 0:
        return {"available": False}

    total = len(failures)
    dominant = max(seg_failures, key=lambda v: (seg_failures[v], seg_amount[v], v))
    ordered = sorted(seg_failures, key=lambda v: (-seg_failures[v], v))
    return {
        "available": True,
        "coverage": round(present / total, 4),  # fraction of failures carrying this dimension
        "segments": {
            v: {"failures": seg_failures[v], "amount": str(seg_amount[v])} for v in ordered
        },
        "dominant": dominant,
        "dominant_failures": seg_failures[dominant],
        "dominant_amount": str(seg_amount[dominant]),
        "dominant_share": round(seg_failures[dominant] / total, 4),
    }


# Prefer the most *specific/actionable* driver first. ``method`` is nearly always fully
# concentrated (degradation is grouped per method), so it's the fallback, not the headline.
_SPECIFICITY = ("error_code", "issuer", "method")
# A dimension only becomes the headline driver if a majority of failures share one segment.
_MAJORITY = 0.5


def _primary_dimension(dimensions: dict) -> dict:
    """Pick the headline degradation driver.

    Prefer ``error_code``, then ``issuer`` — but only when that dimension is available *and* a
    majority of failures concentrate in its dominant segment. Otherwise fall back to ``method``
    (the always-available grouping dimension).
    """
    chosen: tuple[str, dict] | None = None
    for name in _SPECIFICITY[:-1]:  # error_code, issuer
        d = dimensions.get(name, {})
        if d.get("available") and d.get("dominant_share", 0.0) >= _MAJORITY:
            chosen = (name, d)
            break
    if chosen is None:
        d = dimensions.get("method", {})
        if d.get("available"):
            chosen = ("method", d)
    if chosen is None:  # defensive — a signal always has at least a method
        return {"dimension": "method", "segment": "unknown", "share": 0.0, "failures": 0}

    name, d = chosen
    return {
        "dimension": name,
        "segment": d["dominant"],
        "share": d["dominant_share"],
        "failures": d["dominant_failures"],
        "amount": d["dominant_amount"],
    }


def _pct(value: float) -> str:
    return f"{round(value * 100, 1)}%"


def _summary(method: str, failure_rate: float, dimensions: dict, primary: dict) -> str:
    """A concise, deterministic root-cause sentence (used when/if the AI is unavailable)."""
    head = f"{_pct(failure_rate)} failure rate on method '{method}'."
    dim, seg, share = primary["dimension"], primary["segment"], primary.get("share", 0.0)
    if dim == "error_code":
        body = (
            f" {_pct(share)} of failures share error code '{seg}' — a concentrated "
            "gateway/authorization failure class."
        )
    elif dim == "issuer":
        body = (
            f" {_pct(share)} of failures come from issuer '{seg}' — likely an issuer-side "
            "decline rather than a broad outage."
        )
    else:
        body = f" Failures are concentrated on method '{seg}' ({_pct(share)} of failures)."
    unavailable = [
        n for n in ("issuer", "error_code") if not dimensions[n].get("available")
    ]
    tail = f" {', '.join(unavailable)} data unavailable." if unavailable else ""
    return head + body + tail


def _mitigation(primary: dict) -> str:
    """A bounded, human-readable mitigation hint. The executable action is still chosen by the
    AI and gated by the PolicyEngine (typically escalation — RevGuard never auto-disables a
    payment rail)."""
    dim, seg = primary["dimension"], primary["segment"]
    if primary.get("share", 0.0) >= 0.5 and dim in ("error_code", "issuer", "method"):
        return (
            f"Escalate to route around the degraded segment ({dim}={seg}): deprioritize or "
            "temporarily disable it and monitor recovery."
        )
    return "Escalate for manual investigation — failures are not concentrated in one segment."


__all__ = ["PaymentDegradationDetector", "PaymentDegradationConfig"]
