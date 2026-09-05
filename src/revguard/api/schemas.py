"""Pydantic request/response schemas for the RevGuard demo API (Phase 10).

These DTOs are the API's public contract. They are deliberately separate from the domain
models: requests are validated and mapped into domain objects, and domain objects are mapped
back into responses via ``from_domain`` helpers. Nothing here contains secrets, credentials,
or raw provider payloads.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from revguard.audit import AuditEntry
from revguard.domain import (
    ActionType,
    CaseStatus,
    Currency,
    DataProvenance,
    Event,
    EventSource,
    EventType,
    PositiveMoney,
    RecoveryCase,
    RiskLevel,
    WorkflowType,
)
from revguard.policy import CONTACT_ACTIONS, PolicyConfig

# The bounded attempt budget the retry schedule enforces (shown alongside the retry history).
_MAX_ATTEMPTS = PolicyConfig().max_attempts

# Customer-contact actions that require consent — blocked when do_not_contact is set. Sorted so
# the response is deterministic.
_CONTACT_ACTIONS: list[str] = sorted(a.value for a in CONTACT_ACTIONS)

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class EventIn(BaseModel):
    """An incoming revenue/payment event submitted to ``POST /events``."""

    event_type: EventType
    source: EventSource = EventSource.INTERNAL
    occurred_at: datetime | None = None
    customer_id: str | None = None
    payment_id: str | None = None
    order_id: str | None = None
    subscription_id: str | None = None
    invoice_id: str | None = None
    # Positive, finite, ≤2dp money — validated at the API boundary so bad input is a clean
    # 422, never a 500 from the domain ``Event`` rejecting it after the schema passed.
    amount: PositiveMoney | None = None
    currency: Currency | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _amount_currency_paired(self) -> EventIn:
        if (self.amount is None) != (self.currency is None):
            raise ValueError("amount and currency must be provided together")
        return self

    def to_domain(self) -> Event:
        fields = self.model_dump(exclude_none=True)
        return Event(**fields)


class EventAccepted(BaseModel):
    """Result of ingesting an event: which cases were created/updated."""

    event_id: str
    accepted: bool
    cases: list[CaseSummary]


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class CaseSummary(BaseModel):
    """Compact case view for list endpoints."""

    case_id: str
    case_type: WorkflowType
    status: CaseStatus
    is_terminal: bool
    customer_id: str | None
    risk_level: RiskLevel
    currency: Currency
    amount_at_risk: Decimal
    amount_recovered: Decimal
    attempt_count: int
    updated_at: datetime
    # Data origin of the case — so a viewer can never mistake demo/test data for live customer
    # data. ``is_synthetic`` is a convenience flag; it is True *only* for generated demo data.
    provenance: DataProvenance
    is_synthetic: bool

    @classmethod
    def from_domain(cls, case: RecoveryCase) -> CaseSummary:
        return cls(
            case_id=case.case_id,
            case_type=case.case_type,
            status=case.status,
            is_terminal=case.is_terminal,
            customer_id=case.customer_id,
            risk_level=case.signal.risk_level,
            currency=case.currency,
            amount_at_risk=case.amount_at_risk,
            amount_recovered=case.amount_recovered,
            attempt_count=case.attempt_count,
            updated_at=case.updated_at,
            provenance=case.provenance,
            is_synthetic=case.provenance.is_synthetic,
        )


class RetryAttempt(BaseModel):
    """One entry in a case's retry history, reconstructed from the append-only audit log."""

    attempt: int
    action: str | None
    executed_at: datetime
    outcome: str  # "executed" | "recovered" | "pending" | "not_recovered" | "failed"
    next_retry_at: datetime | None = None


def build_retry_history(entries: list[AuditEntry]) -> list[RetryAttempt]:
    """Reconstruct the per-attempt retry history from ordered audit entries.

    Each executed action is an attempt; the following verification/recovery entry supplies its
    outcome, and a ``retry_scheduled`` marker records when the *next* attempt is due. This reads
    only the append-only audit log, so the history is authoritative and survives restarts.
    """
    history: list[RetryAttempt] = []
    n = 0
    for e in entries:
        stage = e.stage.value
        if stage == "execution":
            n += 1
            history.append(
                RetryAttempt(
                    attempt=n, action=e.action, executed_at=e.recorded_at, outcome="executed"
                )
            )
        elif stage == "verification" and history:
            status = e.details.get("verification_status")
            if isinstance(status, str):
                history[-1] = history[-1].model_copy(update={"outcome": status})
        elif stage == "recovery_result" and history:
            history[-1] = history[-1].model_copy(update={"outcome": "recovered"})
        elif stage == "status_change" and e.action == "retry_scheduled" and history:
            nxt = e.details.get("next_retry_at")
            history[-1] = history[-1].model_copy(
                update={"next_retry_at": nxt if isinstance(nxt, str) else None}
            )
    return history


class PromiseOut(BaseModel):
    """A case's promise-to-pay lifecycle for the detail view (never a recovery claim)."""

    status: str  # "promised" | "pending" | "kept" | "missed"
    promised_at: datetime
    recorded_at: datetime
    amount: Decimal | None = None
    reference: str | None = None
    verification_reference: str | None = None
    resolved_at: datetime | None = None

    @classmethod
    def from_domain(cls, promise) -> PromiseOut:  # noqa: ANN001 - domain PromiseToPay
        return cls(
            status=promise.status.value,
            promised_at=promise.promised_at,
            recorded_at=promise.recorded_at,
            amount=promise.amount,
            reference=promise.reference,
            verification_reference=promise.verification_reference,
            resolved_at=promise.resolved_at,
        )


class ProvenanceFacet(BaseModel):
    """One provenance dimension shown in the case detail.

    ``synthetic`` is True when the thing described is not real (generated demo data, or an
    offline/simulated action or verification) — never for a real live-provider object.
    """

    label: str
    synthetic: bool
    detail: str


class CaseProvenance(BaseModel):
    """Provenance of the four things a case is built from, so demo/test data is never mistaken
    for live production customer data: the case, its underlying transaction data, the recovery
    action that executed, and how the payment was verified."""

    case: ProvenanceFacet
    transaction: ProvenanceFacet
    recovery_action: ProvenanceFacet
    payment_verification: ProvenanceFacet


def _data_facet(prov: DataProvenance) -> ProvenanceFacet:
    """The case/transaction facet, derived from the originating signal's data origin."""
    if prov is DataProvenance.SYNTHETIC:
        return ProvenanceFacet(
            label="Synthetic",
            synthetic=True,
            detail="Generated demo data — not real customer money or a real merchant.",
        )
    if prov is DataProvenance.RAZORPAY:
        return ProvenanceFacet(
            label="Razorpay",
            synthetic=False,
            detail="Originated from a real Razorpay object (e.g. a signature-verified webhook).",
        )
    return ProvenanceFacet(
        label="Internal",
        synthetic=False,
        detail="Submitted to the events API by an internal integration.",
    )


def _last_stage(entries: list[AuditEntry], stage: str) -> AuditEntry | None:
    """The most recent non-rejected audit entry for a stage (last attempt wins)."""
    found = None
    for e in entries:
        if e.stage.value == stage and not e.details.get("rejected"):
            found = e
    return found


def _action_facet(entries: list[AuditEntry]) -> ProvenanceFacet:
    """How the recovery action executed: offline demo test double vs a real Razorpay Test Mode
    object. Driven by the audited ground-truth ``provider``/``simulated`` fields, never assumed."""
    e = _last_stage(entries, "execution")
    if e is None:
        return ProvenanceFacet(
            label="None yet",
            synthetic=False,
            detail="No recovery action has executed for this case yet.",
        )
    provider = e.details.get("provider")
    simulated = bool(e.details.get("simulated"))
    if simulated or provider == "mock":
        return ProvenanceFacet(
            label="Demo test double",
            synthetic=True,
            detail=(
                "Executed by the offline demo adapter — a deterministic test double, not a "
                "Razorpay call. No provider was contacted and no money moved."
            ),
        )
    return ProvenanceFacet(
        label="Razorpay Test Mode",
        synthetic=False,
        detail="Executed as a real Razorpay Test Mode object — test money only, never live funds.",
    )


def _verification_facet(
    entries: list[AuditEntry], *, recovered: bool
) -> ProvenanceFacet:
    """How the payment was verified: mock, a simulated Test Mode confirmation, or a real webhook."""
    ver = _last_stage(entries, "verification")
    if ver is None or ver.details.get("verification_status") not in {"recovered", "pending"}:
        return ProvenanceFacet(
            label="Not yet verified",
            synthetic=False,
            detail="Recovery has not been verified; a successful API call alone never counts.",
        )
    source = ver.details.get("source")
    if source == "razorpay_webhook":
        return ProvenanceFacet(
            label="Razorpay webhook",
            synthetic=False,
            detail="Confirmed by a signature-verified Razorpay paid webhook (real provider event).",
        )
    if source == "razorpay_test_simulation":
        return ProvenanceFacet(
            label="Razorpay Test Mode (simulated payment)",
            synthetic=True,
            detail=(
                "Confirmed via a simulated customer payment against the real Razorpay Test Mode "
                "reconciliation path (the same path a live paid webhook uses)."
            ),
        )
    if source == "demo_simulation":
        return ProvenanceFacet(
            label="Demo simulated payment",
            synthetic=True,
            detail=(
                "Confirmed by a simulated payment in demo mode — a deterministic test double, "
                "not a Razorpay interaction."
            ),
        )
    if not recovered:
        return ProvenanceFacet(
            label="Awaiting confirmation",
            synthetic=False,
            detail="Action executed; recovery is confirmed only once the provider reports payment.",
        )
    return ProvenanceFacet(
        label="Simulated verification",
        synthetic=True,
        detail="Confirmed by the offline verifier — no real provider was contacted.",
    )


def build_case_provenance(
    case: RecoveryCase, entries: list[AuditEntry]
) -> CaseProvenance:
    """Assemble the four-facet provenance view for a case from its data origin + audit trail."""
    data = _data_facet(case.provenance)
    return CaseProvenance(
        case=data,
        transaction=data,
        recovery_action=_action_facet(entries),
        payment_verification=_verification_facet(
            entries, recovered=case.status is CaseStatus.RECOVERED
        ),
    )


class CaseDetail(CaseSummary):
    """Full case view for the detail endpoint."""

    current_action: ActionType | None
    current_step: int
    created_at: datetime
    escalated_at: datetime | None
    escalation_reason: str | None
    stopped_at: datetime | None
    stop_reason: str | None
    # Promise-to-pay lifecycle (only for cases where a promise was recorded).
    promise: PromiseOut | None = None
    # Deterministic retry sequencing: the next eligible retry time (if a retry is scheduled),
    # the bounded attempt budget, and the per-attempt history reconstructed from the audit log.
    next_retry_at: datetime | None = None
    max_attempts: int = _MAX_ATTEMPTS
    retry_history: list[RetryAttempt] = []
    # The detector's evidence for *why* this case was raised (e.g. the payment-degradation
    # root-cause breakdown). Read-only, non-secret, JSON-scalar values only.
    signal_evidence: dict[str, Any] = {}
    # Data provenance of the case, its transaction data, the recovery action, and the payment
    # verification — so demo/test data is never mistaken for live production customer data.
    provenance_detail: CaseProvenance
    # Consent / do-not-contact state, surfaced so an operator sees it BEFORE authorizing a
    # customer-contact action. When ``do_not_contact`` is set, the listed contact actions are
    # blocked (the backend refuses to execute them on both the autonomous and human paths).
    do_not_contact: bool
    blocked_contact_actions: list[str]

    @classmethod
    def from_domain(
        cls, case: RecoveryCase, *, audit_entries: list[AuditEntry] | None = None
    ) -> CaseDetail:
        entries = audit_entries or []
        return cls(
            case_id=case.case_id,
            case_type=case.case_type,
            status=case.status,
            is_terminal=case.is_terminal,
            customer_id=case.customer_id,
            risk_level=case.signal.risk_level,
            currency=case.currency,
            amount_at_risk=case.amount_at_risk,
            amount_recovered=case.amount_recovered,
            attempt_count=case.attempt_count,
            updated_at=case.updated_at,
            provenance=case.provenance,
            is_synthetic=case.provenance.is_synthetic,
            current_action=case.current_action,
            current_step=case.current_step,
            created_at=case.created_at,
            escalated_at=case.escalated_at,
            escalation_reason=case.escalation_reason,
            stopped_at=case.stopped_at,
            stop_reason=case.stop_reason.value if case.stop_reason else None,
            next_retry_at=case.next_retry_at,
            max_attempts=_MAX_ATTEMPTS,
            retry_history=build_retry_history(entries),
            promise=PromiseOut.from_domain(case.promise) if case.promise else None,
            signal_evidence=dict(case.signal.evidence),
            provenance_detail=build_case_provenance(case, entries),
            do_not_contact=case.do_not_contact,
            blocked_contact_actions=_CONTACT_ACTIONS if case.do_not_contact else [],
        )


class CaseList(BaseModel):
    cases: list[CaseSummary]
    count: int


class RunResult(BaseModel):
    """Outcome of running the recovery workflow for a case."""

    case: CaseDetail
    recovered: bool
    amount_recovered: Decimal


class AuthorizeIn(BaseModel):
    """A human operator's authorization of one bounded action on an escalated case."""

    action: ActionType
    operator: str = Field(default="operator", min_length=1, max_length=120)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditEntryOut(BaseModel):
    seq: int | None
    stage: str
    actor: str
    action: str | None
    recorded_at: datetime
    correlation_id: str | None
    details: dict[str, Any]

    @classmethod
    def from_domain(cls, entry: AuditEntry) -> AuditEntryOut:
        return cls(
            seq=entry.seq,
            stage=entry.stage.value,
            actor=entry.actor.value,
            action=entry.action,
            recorded_at=entry.recorded_at,
            correlation_id=entry.correlation_id,
            details=dict(entry.details),
        )


class AuditTrail(BaseModel):
    case_id: str
    entries: list[AuditEntryOut]


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


class StatusOut(BaseModel):
    """Runtime mode + provider readiness for the dashboard. Contains no secrets — only the
    mode, the effective provider names, and booleans indicating whether credentials exist."""

    mode: str  # "demo" | "production"
    demo: bool
    ai_provider: str  # effective diagnoser: "mock" in demo, else the configured provider
    ai_configured: bool  # whether the active provider's API key is present
    razorpay_configured: bool  # key + secret present
    razorpay_test_mode: bool  # the configured Razorpay key is a Test Mode key
    webhook_configured: bool  # webhook secret present
    run_ready: bool  # whether POST /cases/{id}/run can execute in the current mode


class WebhookAck(BaseModel):
    status: str
    event: str
    recovered: bool
    event_type: str
    # Reconciliation outcome: whether the verified webhook was matched to a stored case and
    # whether that case was confirmed recovered. `recovered` above reflects the provider event;
    # `reconciled` reflects the case-state change (only ever a verified confirmation).
    matched_case_id: str | None = None
    reconciled: bool = False
    reconcile_reason: str | None = None


# Resolve the forward reference used before ``CaseSummary`` is defined.
EventAccepted.model_rebuild()


__all__ = [
    "EventIn",
    "EventAccepted",
    "CaseSummary",
    "CaseDetail",
    "ProvenanceFacet",
    "CaseProvenance",
    "build_case_provenance",
    "PromiseOut",
    "RetryAttempt",
    "build_retry_history",
    "CaseList",
    "RunResult",
    "AuditEntryOut",
    "AuditTrail",
    "StatusOut",
    "WebhookAck",
]
