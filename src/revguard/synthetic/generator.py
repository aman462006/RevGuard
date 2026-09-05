"""Deterministic synthetic scenario generator for the four revenue-risk workflows.

Given a seed, :func:`build_catalog` returns a fixed, reproducible catalogue of
:class:`Scenario` objects. Each scenario bundles the input events with the outcome the
detectors are expected to produce (signal or not, and — when a signal is expected — the
risk level and amount at risk). The *structure* of the catalogue is fixed; the seed only
perturbs monetary amounts within bands that never cross a severity threshold, so
expectations remain exact while the data stays reproducibly varied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random

from revguard.domain import (
    Currency,
    Event,
    EventSource,
    EventType,
    RiskLevel,
    WorkflowType,
)

DEFAULT_SEED = 20260831
_BASE_TIME = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Scenario:
    """One synthetic case: input events plus the expected detector outcome."""

    name: str
    workflow: WorkflowType
    events: tuple[Event, ...]
    expects_signal: bool
    expected_risk_level: RiskLevel | None = None
    expected_amount_at_risk: Decimal | None = None
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


class _Builder:
    """Stateful, seeded helper that emits deterministic events and scenarios."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = Random(seed)
        self._counter = 0

    # -- primitives ---------------------------------------------------------------------

    def _id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self.seed}_{self._counter:05d}"

    def _at(self, minutes: int) -> datetime:
        return _BASE_TIME + timedelta(minutes=minutes)

    def _amount(self, low: int, high: int) -> Decimal:
        """A 2-dp INR amount uniformly in ``[low, high]`` major units (seeded)."""
        paise = self.rng.randint(low * 100, high * 100)
        return Decimal(paise) / Decimal(100)

    def _event(self, event_type: EventType, minute: int, **fields) -> Event:
        return Event(
            event_id=self._id("evt"),
            event_type=event_type,
            source=EventSource.SYNTHETIC,
            occurred_at=self._at(minute),
            **fields,
        )

    # ---------------------------------------------------------------------------
    # Workflow A — payment degradation
    # ---------------------------------------------------------------------------

    def _payment_window(
        self, method: str, total: int, failures: int, minute0: int
    ) -> tuple[list[Event], Decimal]:
        events: list[Event] = []
        failed_sum = Decimal("0")
        for i in range(total):
            is_failure = i < failures
            amount = self._amount(400, 600)
            if is_failure:
                failed_sum += amount
            events.append(
                self._event(
                    EventType.PAYMENT_FAILED
                    if is_failure
                    else EventType.PAYMENT_SUCCEEDED,
                    minute0 + i,
                    payment_id=self._id("pay"),
                    amount=amount,
                    currency=Currency.INR,
                    metadata={"method": method},
                )
            )
        return events, failed_sum

    def payment_degradation_scenarios(self) -> list[Scenario]:
        # Distinct methods so a full-stream run keeps these windows separate (the engine
        # groups degradation by payment method).
        specs = [
            # name, method, total, failures, expects, risk, tags
            ("A_normal_low_failure_rate", "card", 30, 3, False, None,
             ("normal", "no_signal")),
            ("A_insufficient_sample", "upi", 15, 9, False, None,
             ("boundary", "no_signal")),
            ("A_boundary_exact_threshold", "netbanking", 40, 10, True, RiskLevel.MEDIUM,
             ("boundary",)),
            ("A_medium_degradation", "wallet", 40, 11, True, RiskLevel.MEDIUM, ("risk",)),
            ("A_high_degradation", "amex_card", 40, 18, True, RiskLevel.HIGH, ("risk",)),
            ("A_critical_degradation", "emi", 40, 24, True, RiskLevel.CRITICAL, ("risk",)),
        ]
        out: list[Scenario] = []
        for idx, (name, method, total, failures, expects, risk, tags) in enumerate(specs):
            events, failed_sum = self._payment_window(
                method=method, total=total, failures=failures, minute0=idx * 100
            )
            out.append(
                Scenario(
                    name=name,
                    workflow=WorkflowType.PAYMENT_DEGRADATION,
                    events=tuple(events),
                    expects_signal=expects,
                    expected_risk_level=risk,
                    expected_amount_at_risk=failed_sum if expects else None,
                    note=f"{failures}/{total} {method} payments failed",
                    tags=tags,
                )
            )
        return out

    # ---------------------------------------------------------------------------
    # Workflow B — failed subscription / mandate
    # ---------------------------------------------------------------------------

    def _subscription_failures(
        self, sub_id: str, cust_id: str, count: int, amount: Decimal, minute0: int
    ) -> list[Event]:
        reasons = ["insufficient_funds", "card_expired", "mandate_paused", "bank_decline"]
        return [
            self._event(
                EventType.SUBSCRIPTION_PAYMENT_FAILED,
                minute0 + i * 60,
                customer_id=cust_id,
                subscription_id=sub_id,
                amount=amount,
                currency=Currency.INR,
                metadata={"failure_reason": reasons[i % len(reasons)], "attempt": i + 1},
            )
            for i in range(count)
        ]

    def failed_subscription_scenarios(self) -> list[Scenario]:
        out: list[Scenario] = []

        # Severity by consecutive failure count (amounts kept below the high-value bump).
        count_specs = [
            ("B_single_failure_low", 1, RiskLevel.LOW),
            ("B_two_failures_medium", 2, RiskLevel.MEDIUM),
            ("B_three_failures_high", 3, RiskLevel.HIGH),
            ("B_four_failures_critical", 4, RiskLevel.CRITICAL),
        ]
        for idx, (name, count, risk) in enumerate(count_specs):
            amount = self._amount(800, 2500)
            sub_id = self._id("sub")
            events = self._subscription_failures(
                sub_id, self._id("cust"), count, amount, minute0=idx * 500
            )
            out.append(
                Scenario(
                    name=name,
                    workflow=WorkflowType.FAILED_SUBSCRIPTION,
                    events=tuple(events),
                    expects_signal=True,
                    expected_risk_level=risk,
                    expected_amount_at_risk=amount,
                    note=f"{count} consecutive mandate failures",
                    tags=("risk",),
                )
            )

        # High-value bump: 2 failures (MEDIUM) but amount >= 5000 -> HIGH.
        amount = self._amount(6000, 9000)
        sub_id = self._id("sub")
        events = self._subscription_failures(
            sub_id, self._id("cust"), 2, amount, minute0=5000
        )
        out.append(
            Scenario(
                name="B_high_value_bump",
                workflow=WorkflowType.FAILED_SUBSCRIPTION,
                events=tuple(events),
                expects_signal=True,
                expected_risk_level=RiskLevel.HIGH,
                expected_amount_at_risk=amount,
                note="2 failures but high-value mandate bumps MEDIUM->HIGH",
                tags=("risk", "history"),
            )
        )

        # Recovered: 2 failures then a success -> no signal.
        amount = self._amount(800, 2500)
        sub_id = self._id("sub")
        cust_id = self._id("cust")
        events = self._subscription_failures(sub_id, cust_id, 2, amount, minute0=6000)
        events.append(
            self._event(
                EventType.PAYMENT_SUCCEEDED,
                6000 + 3 * 60,
                customer_id=cust_id,
                subscription_id=sub_id,
                amount=amount,
                currency=Currency.INR,
            )
        )
        out.append(
            Scenario(
                name="B_recovered_no_signal",
                workflow=WorkflowType.FAILED_SUBSCRIPTION,
                events=tuple(events),
                expects_signal=False,
                note="mandate recovered on retry",
                tags=("no_signal", "history"),
            )
        )

        # No failures at all (only a success) -> no signal.
        sub_id = self._id("sub")
        cust_id = self._id("cust")
        events = [
            self._event(
                EventType.PAYMENT_SUCCEEDED,
                7000,
                customer_id=cust_id,
                subscription_id=sub_id,
                amount=self._amount(800, 2500),
                currency=Currency.INR,
            )
        ]
        out.append(
            Scenario(
                name="B_healthy_no_signal",
                workflow=WorkflowType.FAILED_SUBSCRIPTION,
                events=tuple(events),
                expects_signal=False,
                note="healthy subscription, no failures",
                tags=("normal", "no_signal"),
            )
        )
        return out

    # ---------------------------------------------------------------------------
    # Workflow C — checkout abandonment
    # ---------------------------------------------------------------------------

    def _abandonments(
        self,
        order_id: str,
        cust_id: str,
        amount: Decimal,
        count: int,
        stage: str,
        minute0: int,
    ) -> list[Event]:
        return [
            self._event(
                EventType.CHECKOUT_ABANDONED,
                minute0 + i * 30,
                customer_id=cust_id,
                order_id=order_id,
                amount=amount,
                currency=Currency.INR,
                metadata={"abandonment_stage": stage, "attempt": i + 1},
            )
            for i in range(count)
        ]

    def checkout_abandonment_scenarios(self) -> list[Scenario]:
        out: list[Scenario] = []

        tier_specs = [
            ("C_small_cart_low", (200, 900), RiskLevel.LOW, "cart"),
            ("C_medium_cart_medium", (2500, 3500), RiskLevel.MEDIUM, "address"),
            ("C_large_cart_high", (12000, 20000), RiskLevel.HIGH, "payment_details"),
        ]
        for idx, (name, (lo, hi), risk, stage) in enumerate(tier_specs):
            amount = self._amount(lo, hi)
            events = self._abandonments(
                self._id("order"), self._id("cust"), amount, 1, stage, minute0=idx * 300
            )
            out.append(
                Scenario(
                    name=name,
                    workflow=WorkflowType.CHECKOUT_ABANDONMENT,
                    events=tuple(events),
                    expects_signal=True,
                    expected_risk_level=risk,
                    expected_amount_at_risk=amount,
                    note=f"abandoned cart worth ~{amount} INR",
                    tags=("risk",),
                )
            )

        # Repeat abandonment: medium cart, 3 abandons -> bump MEDIUM->HIGH.
        amount = self._amount(2500, 3500)
        events = self._abandonments(
            self._id("order"), self._id("cust"), amount, 3, "payment_details",
            minute0=1000,
        )
        out.append(
            Scenario(
                name="C_repeat_abandon_bump",
                workflow=WorkflowType.CHECKOUT_ABANDONMENT,
                events=tuple(events),
                expects_signal=True,
                expected_risk_level=RiskLevel.HIGH,
                expected_amount_at_risk=amount,
                note="repeat abandonment bumps MEDIUM->HIGH",
                tags=("risk", "history"),
            )
        )

        # Below materiality floor -> no signal.
        amount = self._amount(20, 80)
        events = self._abandonments(
            self._id("order"), self._id("cust"), amount, 1, "cart", minute0=1500
        )
        out.append(
            Scenario(
                name="C_below_floor_no_signal",
                workflow=WorkflowType.CHECKOUT_ABANDONMENT,
                events=tuple(events),
                expects_signal=False,
                note="cart below materiality floor",
                tags=("boundary", "no_signal"),
            )
        )

        # Completed after abandonment -> no signal.
        amount = self._amount(2500, 3500)
        order_id = self._id("order")
        cust_id = self._id("cust")
        events = self._abandonments(order_id, cust_id, amount, 1, "address", minute0=1800)
        events.append(
            self._event(
                EventType.PAYMENT_SUCCEEDED,
                1800 + 60,
                customer_id=cust_id,
                order_id=order_id,
                amount=amount,
                currency=Currency.INR,
            )
        )
        out.append(
            Scenario(
                name="C_completed_no_signal",
                workflow=WorkflowType.CHECKOUT_ABANDONMENT,
                events=tuple(events),
                expects_signal=False,
                note="checkout completed after abandonment",
                tags=("no_signal", "history"),
            )
        )
        return out

    # ---------------------------------------------------------------------------
    # Workflow D — overdue receivables / promise-to-pay
    # ---------------------------------------------------------------------------

    def _overdue(
        self,
        invoice_id: str,
        cust_id: str,
        amount: Decimal,
        days_overdue: int,
        minute0: int,
    ) -> Event:
        due_date = (_BASE_TIME - timedelta(days=days_overdue)).date().isoformat()
        return self._event(
            EventType.INVOICE_OVERDUE,
            minute0,
            customer_id=cust_id,
            invoice_id=invoice_id,
            amount=amount,
            currency=Currency.INR,
            metadata={"days_overdue": days_overdue, "due_date": due_date},
        )

    def _promise(
        self, invoice_id: str, cust_id: str, amount: Decimal, days_from_base: int
    ) -> Event:
        promised = (_BASE_TIME + timedelta(days=days_from_base)).isoformat()
        return self._event(
            EventType.PROMISE_TO_PAY,
            -60,  # promised slightly before the reference notice
            customer_id=cust_id,
            invoice_id=invoice_id,
            amount=amount,
            currency=Currency.INR,
            metadata={"promised_date": promised},
        )

    def overdue_receivable_scenarios(self) -> list[Scenario]:
        out: list[Scenario] = []

        day_specs = [
            ("D_recent_low", 10, RiskLevel.LOW),
            ("D_30_days_medium", 35, RiskLevel.MEDIUM),
            ("D_60_days_high", 65, RiskLevel.HIGH),
            ("D_90_days_critical", 120, RiskLevel.CRITICAL),
        ]
        for idx, (name, days, risk) in enumerate(day_specs):
            amount = self._amount(20000, 200000)
            events = [
                self._overdue(
                    self._id("inv"), self._id("cust"), amount, days, minute0=idx * 100
                )
            ]
            out.append(
                Scenario(
                    name=name,
                    workflow=WorkflowType.OVERDUE_RECEIVABLE,
                    events=tuple(events),
                    expects_signal=True,
                    expected_risk_level=risk,
                    expected_amount_at_risk=amount,
                    note=f"{days} days overdue",
                    tags=("risk",),
                )
            )

        # Active (future-dated) promise lowers HIGH (65 days) -> MEDIUM.
        amount = self._amount(20000, 200000)
        inv_id = self._id("inv")
        cust_id = self._id("cust")
        events = [
            self._overdue(inv_id, cust_id, amount, 65, minute0=1000),
            self._promise(inv_id, cust_id, amount, days_from_base=10),
        ]
        out.append(
            Scenario(
                name="D_active_promise_reduces",
                workflow=WorkflowType.OVERDUE_RECEIVABLE,
                events=tuple(events),
                expects_signal=True,
                expected_risk_level=RiskLevel.MEDIUM,
                expected_amount_at_risk=amount,
                note="active promise-to-pay lowers HIGH->MEDIUM",
                tags=("risk", "history"),
            )
        )

        # Broken (past-due) promise raises MEDIUM (35 days) -> HIGH.
        amount = self._amount(20000, 200000)
        inv_id = self._id("inv")
        cust_id = self._id("cust")
        events = [
            self._overdue(inv_id, cust_id, amount, 35, minute0=1100),
            self._promise(inv_id, cust_id, amount, days_from_base=-5),
        ]
        out.append(
            Scenario(
                name="D_broken_promise_bumps",
                workflow=WorkflowType.OVERDUE_RECEIVABLE,
                events=tuple(events),
                expects_signal=True,
                expected_risk_level=RiskLevel.HIGH,
                expected_amount_at_risk=amount,
                note="broken promise-to-pay raises MEDIUM->HIGH",
                tags=("risk", "history"),
            )
        )

        # Not actually overdue yet -> no signal.
        amount = self._amount(20000, 200000)
        events = [
            self._overdue(self._id("inv"), self._id("cust"), amount, 0, minute0=1200)
        ]
        out.append(
            Scenario(
                name="D_not_overdue_no_signal",
                workflow=WorkflowType.OVERDUE_RECEIVABLE,
                events=tuple(events),
                expects_signal=False,
                note="invoice not past due",
                tags=("boundary", "no_signal"),
            )
        )

        # Paid after the overdue notice -> no signal.
        amount = self._amount(20000, 200000)
        inv_id = self._id("inv")
        cust_id = self._id("cust")
        events = [
            self._overdue(inv_id, cust_id, amount, 45, minute0=1300),
            self._event(
                EventType.PAYMENT_SUCCEEDED,
                1300 + 60,
                customer_id=cust_id,
                invoice_id=inv_id,
                amount=amount,
                currency=Currency.INR,
            ),
        ]
        out.append(
            Scenario(
                name="D_paid_no_signal",
                workflow=WorkflowType.OVERDUE_RECEIVABLE,
                events=tuple(events),
                expects_signal=False,
                note="overdue invoice subsequently paid",
                tags=("no_signal", "history"),
            )
        )
        return out


def build_catalog(seed: int = DEFAULT_SEED) -> list[Scenario]:
    """Build the full, reproducible catalogue of synthetic scenarios for ``seed``."""
    b = _Builder(seed)
    scenarios: list[Scenario] = []
    scenarios += b.payment_degradation_scenarios()
    scenarios += b.failed_subscription_scenarios()
    scenarios += b.checkout_abandonment_scenarios()
    scenarios += b.overdue_receivable_scenarios()
    return scenarios


def generate_events(seed: int = DEFAULT_SEED) -> list[Event]:
    """Flatten the catalogue into a single event stream (e.g. for the DetectionEngine)."""
    return [event for scenario in build_catalog(seed) for event in scenario.events]


__all__ = ["DEFAULT_SEED", "Scenario", "build_catalog", "generate_events"]
