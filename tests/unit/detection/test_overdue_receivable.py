"""Detector D — overdue receivables / promise-to-pay: positive, negative, boundary."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from revguard.detection import OverdueReceivableDetector
from revguard.domain import EventType, RiskLevel, WorkflowType
from tests.unit.detection.conftest import BASE


def _overdue(make_event, days, amount="50000.00", inv="inv_1", cust="cust_1"):
    return make_event(
        EventType.INVOICE_OVERDUE,
        minute=0,
        amount=Decimal(amount),
        invoice_id=inv,
        customer_id=cust,
        metadata={"days_overdue": days},
    )


def _promise(make_event, promised_dt, amount="50000.00", inv="inv_1", cust="cust_1"):
    return make_event(
        EventType.PROMISE_TO_PAY,
        minute=-60,
        amount=Decimal(amount),
        invoice_id=inv,
        customer_id=cust,
        metadata={"promised_date": promised_dt.isoformat()},
    )


def test_recent_low(make_event):
    signal = OverdueReceivableDetector().detect([_overdue(make_event, 10)])
    assert signal is not None
    assert signal.signal_type == WorkflowType.OVERDUE_RECEIVABLE
    assert signal.risk_level == RiskLevel.LOW
    assert signal.invoice_id == "inv_1"
    assert signal.amount_at_risk == Decimal("50000.00")
    assert signal.evidence["days_overdue"] == 10


def test_medium_high_critical(make_event):
    det = OverdueReceivableDetector()
    assert det.detect([_overdue(make_event, 35)]).risk_level == RiskLevel.MEDIUM
    assert det.detect([_overdue(make_event, 65)]).risk_level == RiskLevel.HIGH
    assert det.detect([_overdue(make_event, 120)]).risk_level == RiskLevel.CRITICAL


def test_boundary_30_days_is_medium(make_event):
    signal = OverdueReceivableDetector().detect([_overdue(make_event, 30)])
    assert signal.risk_level == RiskLevel.MEDIUM


def test_active_promise_reduces_severity(make_event):
    # 65 days -> HIGH, but a future-dated promise lowers it to MEDIUM.
    overdue = _overdue(make_event, 65)
    promise = _promise(make_event, BASE + timedelta(days=10))
    signal = OverdueReceivableDetector().detect([overdue, promise])
    assert signal.risk_level == RiskLevel.MEDIUM
    assert signal.evidence["has_active_promise"] is True


def test_broken_promise_bumps_severity(make_event):
    # 35 days -> MEDIUM, but a past-due (broken) promise raises it to HIGH.
    overdue = _overdue(make_event, 35)
    promise = _promise(make_event, BASE - timedelta(days=5))
    signal = OverdueReceivableDetector().detect([overdue, promise])
    assert signal.risk_level == RiskLevel.HIGH
    assert signal.evidence["has_broken_promise"] is True


def test_not_overdue_no_signal(make_event):
    assert OverdueReceivableDetector().detect([_overdue(make_event, 0)]) is None


def test_paid_after_overdue_no_signal(make_event):
    overdue = _overdue(make_event, 45)
    paid = make_event(
        EventType.PAYMENT_SUCCEEDED,
        minute=60,
        amount=Decimal("50000.00"),
        invoice_id="inv_1",
        customer_id="cust_1",
    )
    assert OverdueReceivableDetector().detect([overdue, paid]) is None
