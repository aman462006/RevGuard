"""Payment-degradation root-cause segmentation: breakdown, dominant driver, missing data.

These assert the detector goes beyond "failures increased": it segments by method / issuer /
error code (only where data exists), quantifies the dominant segment's contribution, marks
unavailable dimensions, and produces a concise summary + bounded mitigation.
"""

from __future__ import annotations

from decimal import Decimal

from revguard.detection import PaymentDegradationDetector
from revguard.diagnosis.prompts import build_case_briefing
from revguard.domain import (
    Currency,
    EventType,
    RecoveryCase,
    WorkflowType,
)


def _fail(make_event, minute, *, method="card", issuer=None, error=None, amount="800.00"):
    meta = {"method": method}
    if issuer is not None:
        meta["issuer"] = issuer
    if error is not None:
        meta["error_code"] = error
    return make_event(
        EventType.PAYMENT_FAILED, minute=minute, amount=Decimal(amount), metadata=meta
    )


def _ok(make_event, minute, *, method="card"):
    return make_event(
        EventType.PAYMENT_SUCCEEDED, minute=minute, amount=Decimal("800.00"),
        metadata={"method": method},
    )


def _detect(events):
    signal = PaymentDegradationDetector().detect(events)
    assert signal is not None
    return signal


def test_segments_failures_by_method_issuer_and_error_code(make_event):
    # 16 failures, 4 successes. 10 failures share error 'gateway_timeout' + issuer 'HDFC'.
    events = []
    for i in range(10):
        events.append(_fail(make_event, i, issuer="HDFC", error="gateway_timeout"))
    for i in range(10, 16):
        events.append(_fail(make_event, i, issuer="ICICI", error="insufficient_funds"))
    for i in range(16, 20):
        events.append(_ok(make_event, i))

    rc = _detect(events).evidence["root_cause"]
    err = rc["dimensions"]["error_code"]
    issuer = rc["dimensions"]["issuer"]

    assert err["available"] is True
    assert err["dominant"] == "gateway_timeout"
    assert err["dominant_failures"] == 10
    assert err["dominant_share"] == round(10 / 16, 4)
    assert err["segments"]["gateway_timeout"]["amount"] == str(Decimal("800.00") * 10)
    assert issuer["available"] is True
    assert issuer["dominant"] == "HDFC"


def test_dominant_root_cause_is_the_most_concentrated_dimension(make_event):
    # Error code is fully concentrated (16/16) while issuer is split — error_code should win.
    events = []
    for i in range(16):
        issuer = "HDFC" if i % 2 == 0 else "ICICI"
        events.append(_fail(make_event, i, issuer=issuer, error="do_not_honour"))
    for i in range(16, 20):
        events.append(_ok(make_event, i))

    rc = _detect(events).evidence["root_cause"]
    assert rc["primary"]["dimension"] == "error_code"
    assert rc["primary"]["segment"] == "do_not_honour"
    assert rc["primary"]["share"] == 1.0
    # A concentrated driver yields a targeted (not generic) mitigation.
    assert "route around" in rc["suggested_mitigation"]


def test_missing_dimensions_are_marked_unavailable_not_invented(make_event):
    # Only 'method' present — issuer and error_code data are absent and must NOT be fabricated.
    events = [_fail(make_event, i) for i in range(16)] + [_ok(make_event, i) for i in range(16, 20)]
    signal = _detect(events)
    rc = signal.evidence["root_cause"]

    assert rc["dimensions"]["method"]["available"] is True
    assert rc["dimensions"]["issuer"] == {"available": False}
    assert rc["dimensions"]["error_code"] == {"available": False}
    assert signal.evidence["issuer_data_available"] is False
    assert signal.evidence["error_code_data_available"] is False
    assert rc["primary"]["dimension"] == "method"
    assert "unavailable" in rc["summary"]


def test_partial_error_code_coverage_is_reported(make_event):
    # Half the failures carry an error code; coverage must reflect that (no invented codes).
    events = []
    for i in range(8):
        events.append(_fail(make_event, i, error="gateway_timeout"))
    for i in range(8, 16):
        events.append(_fail(make_event, i))  # no error code on these
    for i in range(16, 20):
        events.append(_ok(make_event, i))

    err = _detect(events).evidence["root_cause"]["dimensions"]["error_code"]
    assert err["available"] is True
    assert err["coverage"] == round(8 / 16, 4)  # only 8/16 failures tagged
    assert err["dominant_failures"] == 8


def test_root_cause_reaches_the_diagnosis_briefing(make_event):
    # The AI diagnosis prompt must receive the concentrated driver as structured, scalar data.
    events = [
        _fail(make_event, i, issuer="HDFC", error="gateway_timeout") for i in range(12)
    ] + [_fail(make_event, i) for i in range(12, 16)] + [_ok(make_event, i) for i in range(16, 20)]
    signal = _detect(events)
    case = RecoveryCase(
        case_id="case_deg",
        case_type=WorkflowType.PAYMENT_DEGRADATION,
        customer_id=None,
        signal=signal,
        amount_at_risk=signal.amount_at_risk,
        currency=Currency.INR,
    )

    briefing = build_case_briefing(case)
    # Scalar summary is forwarded, and the compact structured breakdown is attached.
    assert "root_cause_summary" in briefing["evidence"]
    assert briefing["evidence"]["primary_segment"] == "gateway_timeout"
    rcb = briefing["root_cause_breakdown"]
    assert rcb["error_code"]["dominant"] == "gateway_timeout"
    assert rcb["issuer"]["available"] is True
    # The full segment map is NOT dumped into the prompt (kept bounded).
    assert "segments" not in rcb["error_code"]


# -- AI output validation (Gemini recommends only; malformed output fails closed) -------
def _degradation_case(make_event) -> RecoveryCase:
    events = [
        _fail(make_event, i, issuer="HDFC", error="gateway_timeout") for i in range(12)
    ] + [_ok(make_event, i) for i in range(12, 20)]
    signal = _detect(events)
    return RecoveryCase(
        case_id="case_deg_ai",
        case_type=WorkflowType.PAYMENT_DEGRADATION,
        customer_id=None,
        signal=signal,
        amount_at_risk=signal.amount_at_risk,
        currency=Currency.INR,
    )


class _FakeGemini:
    """Minimal google-genai client stand-in: returns a canned JSON body as response text."""

    def __init__(self, text: str) -> None:
        from types import SimpleNamespace

        self._text = text
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(parts=[SimpleNamespace(text=self._text)])
                )
            ]
        )


def test_ai_valid_degradation_recommendation_is_accepted(make_event):
    from revguard.diagnosis.gemini_provider import GeminiProvider
    from revguard.domain import ActionType

    client = _FakeGemini(
        '{"action_type": "recommend_escalation", '
        '"rationale": "gateway_timeout dominates card failures; escalate to reroute", '
        '"confidence": 0.9}'
    )
    proposal = GeminiProvider(model="m", client=client).diagnose(_degradation_case(make_event))
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.confidence == 0.9
    assert proposal.case_id == "case_deg_ai"


def test_ai_invalid_action_fails_closed_to_escalation(make_event):
    from revguard.diagnosis.gemini_provider import GeminiProvider
    from revguard.domain import ActionType

    # An action outside the allowed enum must never execute — it degrades to a safe escalation.
    client = _FakeGemini('{"action_type": "disable_all_payments", "rationale": "x", '
                         '"confidence": 0.95}')
    proposal = GeminiProvider(model="m", client=client).diagnose(_degradation_case(make_event))
    assert proposal.action_type is ActionType.RECOMMEND_ESCALATION
    assert proposal.confidence == 0.0  # safe fallback, not the model's claimed confidence
