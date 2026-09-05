"""FastAPI application for the RevGuard demo (Phase 10).

A **thin** HTTP layer over the existing services: detection, the recovery orchestrator, the
policy engine, persistence/audit, metrics, and the Phase 9 Razorpay webhook core. Endpoints
validate input with Pydantic schemas, call one service, and map the result back — they hold
no business logic and never bypass the PolicyEngine (recovery only ever runs through
``RecoveryAgent.process_case``). Secrets are never returned or logged.

FastAPI is imported here (module top) because this module is the API entry point; the core
engine never imports it. ``from __future__ import annotations`` is intentionally omitted so
FastAPI can resolve the ``Request``/``Header``/``Depends`` parameter types at definition time.
"""

import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from revguard.api.dependencies import (
    get_database,
    get_ingest_agent,
    get_run_agent,
    get_settings,
    get_verify_agent,
)
from revguard.api.schemas import (
    AuditEntryOut,
    AuditTrail,
    AuthorizeIn,
    CaseDetail,
    CaseList,
    CaseSummary,
    EventAccepted,
    EventIn,
    RunResult,
    StatusOut,
    WebhookAck,
)
from revguard.audit import AuditLog
from revguard.config import Settings
from revguard.evaluation import EvaluationReport, evaluate
from revguard.integrations.razorpay.webhook import (
    map_webhook_to_event,
    map_webhook_to_outcome,
    parse_webhook,
)
from revguard.metrics import (
    BatchMetrics,
    RecoveryAnalytics,
    compute_metrics,
    compute_recovery_analytics,
)
from revguard.orchestrator import (
    HumanReviewError,
    ReconciliationResult,
    RecoveryAgent,
    RecoveryConfirmation,
)
from revguard.persistence import CaseRepository, Database, EventRepository, reset_all
from revguard.synthetic import DEFAULT_SEED

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Razorpay-Signature"


def create_api_app(
    settings: Settings | None = None, *, database: Database | None = None
) -> FastAPI:
    """Build the RevGuard demo API. A ``database`` may be injected (tests)."""
    settings = settings or Settings()
    db = database or Database(settings.database_url)
    db.create_all()

    app = FastAPI(title="RevGuard API", version="0.1.0")
    app.state.settings = settings
    app.state.db = db

    # Allow the local dashboard (Vite dev server) to call the API from the browser. This only
    # controls cross-origin access; no endpoint returns secrets, so nothing sensitive is exposed.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allow_origins),
            allow_methods=["GET", "POST"],
            allow_headers=["*"],
        )

    if settings.is_demo:
        logger.warning(
            "RevGuard is running in DEMO mode: POST /cases/{id}/run uses OFFLINE MOCKS, "
            "not the live AI (Gemini) + Razorpay Test Mode path."
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", response_model=StatusOut)
    def status(settings: Settings = Depends(get_settings)) -> StatusOut:
        # Non-secret readiness snapshot for the dashboard: mode, effective provider, and
        # whether the credentials needed by the current mode are present.
        key_id = settings.razorpay_key_id or ""
        rzp_configured = bool(settings.razorpay_key_id and settings.razorpay_key_secret)
        rzp_test = key_id.startswith("rzp_test_")
        provider = (settings.ai_provider or "mock").strip().lower()
        _provider_keys = {
            "groq": settings.groq_api_key,
            "gemini": settings.gemini_api_key,
            "anthropic": settings.anthropic_api_key,
        }
        ai_configured = bool(_provider_keys.get(provider))
        demo = settings.is_demo
        run_ready = True if demo else (ai_configured and rzp_configured and rzp_test)
        return StatusOut(
            mode="demo" if demo else "production",
            demo=demo,
            ai_provider="mock" if demo else provider,
            ai_configured=ai_configured,
            razorpay_configured=rzp_configured,
            razorpay_test_mode=rzp_test,
            webhook_configured=bool(settings.razorpay_webhook_secret),
            run_ready=run_ready,
        )

    # -- Events -------------------------------------------------------------------------

    @app.post("/events", response_model=EventAccepted, status_code=201)
    def post_event(
        payload: EventIn,
        agent: RecoveryAgent = Depends(get_ingest_agent),
    ) -> EventAccepted:
        event = payload.to_domain()
        cases = agent.ingest_event(event)
        return EventAccepted(
            event_id=event.event_id,
            accepted=True,
            cases=[CaseSummary.from_domain(c) for c in cases],
        )

    # -- Cases --------------------------------------------------------------------------

    @app.get("/cases", response_model=CaseList)
    def list_cases(db: Database = Depends(get_database)) -> CaseList:
        with db.session() as session:
            cases = CaseRepository(session).list_all()
        summaries = [CaseSummary.from_domain(c) for c in cases]
        return CaseList(cases=summaries, count=len(summaries))

    @app.get("/cases/{case_id}", response_model=CaseDetail)
    def get_case(case_id: str, db: Database = Depends(get_database)) -> CaseDetail:
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
            if case is None:
                raise HTTPException(status_code=404, detail="case not found")
            # Reconstruct the retry history from the append-only audit log for the detail view.
            entries = AuditLog(session).for_case(case_id)
        return CaseDetail.from_domain(case, audit_entries=entries)

    @app.get("/cases/{case_id}/audit", response_model=AuditTrail)
    def get_case_audit(case_id: str, db: Database = Depends(get_database)) -> AuditTrail:
        with db.session() as session:
            if CaseRepository(session).get(case_id) is None:
                raise HTTPException(status_code=404, detail="case not found")
            entries = AuditLog(session).for_case(case_id)
        return AuditTrail(
            case_id=case_id,
            entries=[AuditEntryOut.from_domain(e) for e in entries],
        )

    @app.post("/cases/{case_id}/run", response_model=RunResult)
    def run_case(
        case_id: str,
        db: Database = Depends(get_database),
        agent: RecoveryAgent = Depends(get_run_agent),
    ) -> RunResult:
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        # Runs the full pipeline: diagnosis (live AI provider, Gemini) → PolicyEngine gate → execute
        # (only on APPROVE) → verification. The agent owns all safety rules.
        result = agent.process_case(case)
        with db.session() as session:
            entries = AuditLog(session).for_case(case_id)
        return RunResult(
            case=CaseDetail.from_domain(result, audit_entries=entries),
            recovered=result.status.value == "recovered",
            amount_recovered=result.amount_recovered,
        )

    @app.post("/cases/{case_id}/authorize", response_model=RunResult)
    def authorize_case(
        case_id: str,
        payload: AuthorizeIn,
        db: Database = Depends(get_database),
        agent: RecoveryAgent = Depends(get_run_agent),
    ) -> RunResult:
        # Human-in-the-loop: an operator authorizes one bounded action on an ESCALATED case.
        # The executor/verifier safety still applies and recovery is verification-based; the AI
        # and PolicyEngine are not bypassed for autonomous runs — this edge is human-only.
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        try:
            result = agent.authorize(case, payload.action, operator=payload.operator)
        except HumanReviewError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        with db.session() as session:
            entries = AuditLog(session).for_case(case_id)
        return RunResult(
            case=CaseDetail.from_domain(result, audit_entries=entries),
            recovered=result.status.value == "recovered",
            amount_recovered=result.amount_recovered,
        )

    @app.post("/cases/{case_id}/check_promise", response_model=RunResult)
    def check_promise(
        case_id: str,
        db: Database = Depends(get_database),
        agent: RecoveryAgent = Depends(get_run_agent),
    ) -> RunResult:
        # Advance a promise-to-pay: on/after the promised time this verifies the actual payment
        # state (via the existing verifier) and either recovers the case or routes a missed
        # promise through the PolicyEngine escalate/stop path. Deterministic; no background job.
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.promise is None:
            raise HTTPException(status_code=409, detail="case has no promise-to-pay to check")
        agent.check_due_promise(case)
        with db.session() as session:
            updated = CaseRepository(session).get(case_id)
            entries = AuditLog(session).for_case(case_id)
        if updated is None:  # pragma: no cover - just refetched
            raise HTTPException(status_code=404, detail="case not found")
        return RunResult(
            case=CaseDetail.from_domain(updated, audit_entries=entries),
            recovered=updated.status.value == "recovered",
            amount_recovered=updated.amount_recovered,
        )

    @app.post("/cases/{case_id}/simulate_payment", response_model=RunResult)
    def simulate_payment(
        case_id: str,
        db: Database = Depends(get_database),
        agent: RecoveryAgent = Depends(get_run_agent),
        settings: Settings = Depends(get_settings),
    ) -> RunResult:
        # DEMO ONLY deterministic test double: simulate the customer completing the pending
        # payment, driving a WAITING case to RECOVERED through the same verified reconciliation
        # path a live *.paid webhook uses. It is deliberately NOT a production verification
        # shortcut — in production, recovery is confirmed only by Razorpay's verified paid
        # status (webhook), and no frontend action may fabricate it.
        if not settings.is_demo:
            raise HTTPException(
                status_code=409,
                detail=(
                    "payment simulation is only available in demo mode; in production, "
                    "recovery is confirmed by Razorpay's verified paid status"
                ),
            )
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.status.value not in {"waiting", "action_executing"}:
            raise HTTPException(
                status_code=409,
                detail=f"case is {case.status.value}, not awaiting payment confirmation",
            )
        agent.simulate_test_recovery(case, source="demo_simulation")
        with db.session() as session:
            updated = CaseRepository(session).get(case_id)
            entries = AuditLog(session).for_case(case_id)
        if updated is None:  # pragma: no cover - just refetched
            raise HTTPException(status_code=404, detail="case not found")
        return RunResult(
            case=CaseDetail.from_domain(updated, audit_entries=entries),
            recovered=updated.status.value == "recovered",
            amount_recovered=updated.amount_recovered,
        )

    @app.post("/cases/{case_id}/mark_unpaid", response_model=RunResult)
    def mark_unpaid(
        case_id: str,
        db: Database = Depends(get_database),
        agent: RecoveryAgent = Depends(get_run_agent),
        settings: Settings = Depends(get_settings),
    ) -> RunResult:
        # DEMO ONLY deterministic test double: record that the customer did NOT pay the pending
        # intervention. Produces a NOT_RECOVERED verification (recovered amount stays 0), then
        # lets the existing PolicyEngine + bounded-retry logic decide the next step (another
        # attempt, or a terminal STOP/ESCALATE). It never fabricates a payment or a recovered
        # amount. In production, non-payment is established only by Razorpay's verified status.
        if not settings.is_demo:
            raise HTTPException(
                status_code=409,
                detail=(
                    "unpaid simulation is only available in demo mode; in production, "
                    "payment status is established by Razorpay"
                ),
            )
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.status.value not in {"waiting", "action_executing"}:
            raise HTTPException(
                status_code=409,
                detail=f"case is {case.status.value}, not awaiting payment confirmation",
            )
        agent.verify_unpaid(case, source="demo_unpaid_simulation")
        with db.session() as session:
            updated = CaseRepository(session).get(case_id)
            entries = AuditLog(session).for_case(case_id)
        if updated is None:  # pragma: no cover - just refetched
            raise HTTPException(status_code=404, detail="case not found")
        return RunResult(
            case=CaseDetail.from_domain(updated, audit_entries=entries),
            recovered=updated.status.value == "recovered",
            amount_recovered=updated.amount_recovered,
        )

    @app.post("/cases/{case_id}/recheck_payment", response_model=RunResult)
    def recheck_payment(
        case_id: str,
        db: Database = Depends(get_database),
        agent: RecoveryAgent = Depends(get_verify_agent),
    ) -> RunResult:
        # Provider-backed status re-poll (no fabrication, available in any mode). Asks the
        # configured verifier for the current status of the case's last intervention: a verified
        # paid status recovers the case with the verified amount; anything else leaves it
        # WAITING. This complements the *.paid webhook so recovery can be confirmed by polling
        # Razorpay's real status when a webhook has not (yet) been delivered. It uses the
        # AI-independent verification agent — a broken/unconfigured AI never blocks confirming a
        # genuine payment.
        with db.session() as session:
            case = CaseRepository(session).get(case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="case not found")
        if case.status.value not in {"waiting", "action_executing"}:
            raise HTTPException(
                status_code=409,
                detail=f"case is {case.status.value}, not awaiting payment confirmation",
            )
        agent.recheck_payment(case)
        with db.session() as session:
            updated = CaseRepository(session).get(case_id)
            entries = AuditLog(session).for_case(case_id)
        if updated is None:  # pragma: no cover - just refetched
            raise HTTPException(status_code=404, detail="case not found")
        return RunResult(
            case=CaseDetail.from_domain(updated, audit_entries=entries),
            recovered=updated.status.value == "recovered",
            amount_recovered=updated.amount_recovered,
        )

    @app.post("/reset")
    def reset(db: Database = Depends(get_database)) -> dict[str, int]:
        # Clear all cases, events, and audit history so the model can be run from scratch. Events
        # are cleared too because detection re-scans stored events (leaving them would re-create
        # the same cases). Returns the counts removed. This never touches secrets or config.
        with db.session() as session:
            return reset_all(session)

    # -- Metrics ------------------------------------------------------------------------

    @app.get("/metrics", response_model=BatchMetrics)
    def get_metrics(db: Database = Depends(get_database)) -> BatchMetrics:
        with db.session() as session:
            cases = CaseRepository(session).list_all()
        return compute_metrics("revguard", cases)

    @app.get("/analytics", response_model=RecoveryAnalytics)
    def get_analytics(db: Database = Depends(get_database)) -> RecoveryAnalytics:
        # Deterministic operational analytics computed only from persisted cases + the
        # append-only audit log. No AI, no live calls, no separate analytics store.
        with db.session() as session:
            cases = CaseRepository(session).list_all()
            entries = AuditLog(session).all()
        return compute_recovery_analytics(cases, entries)

    # -- Evaluation (reuses the Phase 8 offline Baseline-vs-RevGuard harness) ------------

    @app.get("/evaluation", response_model=EvaluationReport)
    def get_evaluation(seed: int = Query(default=DEFAULT_SEED, ge=0)) -> EvaluationReport:
        # Deterministic, offline comparison over the seeded synthetic dataset (MockDiagnoser).
        # This is the evaluation harness, independent of the live per-case recovery path.
        return evaluate(seed=seed)

    # -- Razorpay webhook (reuses Phase 9 verification core) ----------------------------

    @app.post("/webhooks/razorpay", response_model=WebhookAck)
    async def razorpay_webhook(
        request: Request,
        db: Database = Depends(get_database),
        settings: Settings = Depends(get_settings),
        agent: RecoveryAgent = Depends(get_ingest_agent),
        x_razorpay_signature: str | None = Header(default=None, alias=SIGNATURE_HEADER),
    ) -> WebhookAck:
        secret = settings.razorpay_webhook_secret
        if not secret:
            raise HTTPException(status_code=503, detail="webhook verification is not configured")

        raw = await request.body()
        try:
            # Signature-gated: an unverified body never reaches mapping. We never trust a
            # client-provided "success" — recovery is confirmed only by verified status.
            body = parse_webhook(raw, x_razorpay_signature or "", secret)
        except Exception:
            logger.warning("rejected razorpay webhook: signature/parse failure")
            raise HTTPException(
                status_code=400, detail="invalid webhook signature or payload"
            ) from None

        outcome = map_webhook_to_outcome(body)
        event = map_webhook_to_event(body)
        with db.session() as session:
            EventRepository(session).add_if_absent(event)  # record the verified event

        # Reconcile a verified *paid* webhook to the case it belongs to. Only a signature-checked
        # paid event with a provider reference confirms recovery; failures/others are recorded
        # only. The agent enforces the state machine (no gate bypass).
        recon: ReconciliationResult | None = None
        confirmation = _confirmation_from_outcome(outcome)
        if confirmation is not None:
            recon = agent.reconcile_recovery(confirmation)

        return WebhookAck(
            status="ok",
            event=outcome.event,
            recovered=outcome.recovered,
            event_type=event.event_type.value,
            matched_case_id=recon.case_id if recon else None,
            reconciled=bool(recon and recon.reconciled),
            reconcile_reason=recon.reason if recon else None,
        )

    return app


def _confirmation_from_outcome(outcome) -> RecoveryConfirmation | None:  # noqa: ANN001
    """Build a :class:`RecoveryConfirmation` from a verified *paid* webhook outcome.

    Returns ``None`` for non-paid events or when no provider reference is present (nothing to
    prove a collection), so those are recorded without touching case state.
    """
    if not outcome.recovered:
        return None
    reference = outcome.payment_id or outcome.order_id or outcome.payment_link_id
    if not reference:
        return None
    return RecoveryConfirmation(
        reference=reference,
        amount=outcome.amount,
        currency=outcome.currency,
        case_reference=outcome.case_reference,
        subscription_id=outcome.subscription_id,
        order_id=outcome.order_id,
        invoice_id=outcome.invoice_id,
        payment_id=outcome.payment_id,
        source="razorpay_webhook",
    )


__all__ = ["create_api_app", "SIGNATURE_HEADER"]
