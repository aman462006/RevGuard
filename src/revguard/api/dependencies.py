"""FastAPI dependencies for the RevGuard demo API (Phase 10).

Wiring only — no business logic. Dependencies hand endpoints the shared :class:`Database`,
the settings, and the recovery agents. Two agents are distinguished on purpose:

* the **ingest** agent runs detection only (no AI/payments), so ``POST /events`` needs no
  external credentials;
* the **run** agent is the real integrated path (the configured live AI provider — Gemini by
  default — plus Razorpay Test Mode) reused from :func:`build_integrated_agent`, so
  ``POST /cases/{id}/run`` diagnoses with the real AI API and keeps the PolicyEngine as the
  mandatory gate.

All of these are overridden in tests with mock-wired equivalents, so no real external service
is contacted. A misconfigured integrated path surfaces as HTTP 503, never a leaked error.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from revguard.config import Settings
from revguard.diagnosis import MockDiagnoser
from revguard.domain import VerificationStatus
from revguard.execution import MockAdapter
from revguard.integrations.razorpay import (
    IntegrationError,
    RazorpayConfigError,
    RazorpayTestAdapter,
    RazorpayVerifier,
    build_integrated_agent,
    create_client,
    require_test_credentials,
)
from revguard.orchestrator import RecoveryAgent
from revguard.persistence import Database
from revguard.verification import MockVerifier

logger = logging.getLogger(__name__)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_database(request: Request) -> Database:
    return request.app.state.db


def get_ingest_agent(request: Request) -> RecoveryAgent:
    """Detection-only agent for event ingestion (never calls AI or a payment provider)."""
    return RecoveryAgent(request.app.state.db)


def get_run_agent(request: Request) -> RecoveryAgent:
    """The recovery agent used by ``POST /cases/{id}/run``.

    * **demo mode** (``REVGUARD_MODE=demo``): the real PolicyEngine wired to the offline
      mock diagnoser/adapter/verifier — a clearly-labelled rehearsal path.
    * **production mode** (default): the integrated live-AI (Gemini by default) + Razorpay Test
      Mode agent. If it is not fully configured it raises HTTP 503 — it never silently falls
      back to the mocks.
    """
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db

    if settings.is_demo:
        # Demo mirrors production's honesty: a successful action (e.g. creating a payment link)
        # is only PENDING recovery — the case waits for a verified payment. Recovery is declared
        # only when the operator confirms the (simulated) payment, never on link creation alone.
        return RecoveryAgent(
            db,
            diagnoser=MockDiagnoser(),
            adapter=MockAdapter(),
            verifier=MockVerifier(default=VerificationStatus.PENDING),
        )

    try:
        return build_integrated_agent(db, settings)
    except (IntegrationError, RazorpayConfigError):
        logger.warning("integrated recovery path is not configured; returning 503")
        raise HTTPException(
            status_code=503,
            detail="recovery integration is not configured",
        ) from None


def get_verify_agent(request: Request) -> RecoveryAgent:
    """Agent for provider-backed payment verification (``POST /cases/{id}/recheck_payment``).

    Confirming whether a *real* payment was collected never diagnoses, so — unlike
    :func:`get_run_agent` — this must **not** require the AI provider. A broken or unconfigured
    AI must never block confirming a genuine Razorpay payment. In demo it uses the offline
    PENDING verifier; in production it wires only the Razorpay Test Mode verifier and returns
    HTTP 503 solely when Razorpay itself is not configured (never for the AI).
    """
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db

    if settings.is_demo:
        return RecoveryAgent(
            db,
            diagnoser=MockDiagnoser(),
            adapter=MockAdapter(),
            verifier=MockVerifier(default=VerificationStatus.PENDING),
        )

    try:
        require_test_credentials(settings)  # Razorpay Test Mode only; independent of the AI
        client = create_client(settings)
    except RazorpayConfigError:
        logger.warning("razorpay verification path is not configured; returning 503")
        raise HTTPException(
            status_code=503,
            detail="razorpay verification is not configured",
        ) from None
    return RecoveryAgent(
        db,
        diagnoser=MockDiagnoser(),  # never called on the verification path
        adapter=RazorpayTestAdapter(client),
        verifier=RazorpayVerifier(client),
    )


__all__ = [
    "get_settings",
    "get_database",
    "get_ingest_agent",
    "get_run_agent",
    "get_verify_agent",
]
