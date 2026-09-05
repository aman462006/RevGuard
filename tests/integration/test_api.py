"""RevGuard demo API (Phase 10): thin HTTP layer over the existing services.

Every test runs against a temporary SQLite database with the external services mocked — the
run agent is wired with MockDiagnoser/MockAdapter/MockVerifier via dependency overrides, so no
real Anthropic or Razorpay call is made. The PolicyEngine is the real one (never bypassed).
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from revguard.api.app import create_api_app  # noqa: E402
from revguard.api.dependencies import (  # noqa: E402
    get_ingest_agent,
    get_run_agent,
    get_verify_agent,
)
from revguard.config import Settings  # noqa: E402
from revguard.diagnosis import MockDiagnoser  # noqa: E402
from revguard.domain import CaseStatus, VerificationStatus  # noqa: E402
from revguard.execution import MockAdapter  # noqa: E402
from revguard.orchestrator import RecoveryAgent  # noqa: E402
from revguard.persistence import CaseRepository, Database  # noqa: E402
from revguard.verification import MockVerifier  # noqa: E402

_WEBHOOK_SECRET = "whsec_test_api"


@pytest.fixture
def api(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'api.db'}")
    db.create_all()
    settings = Settings(_env_file=None, razorpay_webhook_secret=_WEBHOOK_SECRET)
    app = create_api_app(settings, database=db)

    # Mock the external services: a recovery agent that always verifies RECOVERED, wired to
    # the SAME database the API reads from. No network, no real AI/payment.
    mock_agent = RecoveryAgent(
        db,
        diagnoser=MockDiagnoser(),
        adapter=MockAdapter(),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
    )
    app.dependency_overrides[get_run_agent] = lambda: mock_agent
    app.dependency_overrides[get_ingest_agent] = lambda: mock_agent

    with TestClient(app) as client:
        yield client
    db.dispose()


def _subscription_event(amount: str = "1500.00") -> dict:
    return {
        "event_type": "subscription_payment_failed",
        "source": "internal",
        "customer_id": "cust_1",
        "subscription_id": "sub_1",
        "amount": amount,
        "currency": "INR",
        "metadata": {"failure_reason": "card_declined"},
    }


def _overdue_event(amount: str = "90000.00") -> dict:
    return {
        "event_type": "invoice_overdue",
        "source": "internal",
        "customer_id": "cust_2",
        "invoice_id": "inv_1",
        "amount": amount,
        "currency": "INR",
        "metadata": {"days_overdue": 65},
    }


def _create_subscription_case(api: TestClient) -> str:
    resp = api.post("/events", json=_subscription_event())
    assert resp.status_code == 201
    cases = resp.json()["cases"]
    assert cases, "expected the failed-subscription event to create a case"
    return cases[0]["case_id"]


# -- health + events --------------------------------------------------------------------


def test_health(api: TestClient):
    assert api.get("/health").json() == {"status": "ok"}


def test_post_event_creates_case(api: TestClient):
    resp = api.post("/events", json=_subscription_event())
    assert resp.status_code == 201
    body = resp.json()
    assert body["accepted"] is True
    assert body["cases"][0]["case_type"] == "failed_subscription"
    assert body["cases"][0]["amount_at_risk"] == "1500.00"


def test_post_event_requires_amount_and_currency_together(api: TestClient):
    bad = _subscription_event()
    bad.pop("currency")  # amount without currency
    assert api.post("/events", json=bad).status_code == 422


# -- listing + detail -------------------------------------------------------------------


def test_list_and_get_case(api: TestClient):
    case_id = _create_subscription_case(api)

    listed = api.get("/cases").json()
    assert listed["count"] == 1
    assert listed["cases"][0]["case_id"] == case_id

    detail = api.get(f"/cases/{case_id}")
    assert detail.status_code == 200
    assert detail.json()["case_id"] == case_id
    assert detail.json()["status"] == "detected"


def test_get_missing_case_is_404(api: TestClient):
    assert api.get("/cases/nope").status_code == 404
    assert api.get("/cases/nope/audit").status_code == 404
    assert api.post("/cases/nope/run").status_code == 404


# -- data provenance (demo/test data is never mistaken for live customer data) -----------


def _synthetic_event() -> dict:
    ev = _subscription_event()
    ev["source"] = "synthetic"
    ev["subscription_id"] = "sub_synthetic"
    return ev


def test_internal_source_case_is_not_labelled_synthetic(api: TestClient):
    # An event submitted by an internal integration is real data — it must never be flagged
    # synthetic anywhere in the API contract.
    case_id = _create_subscription_case(api)

    summary = api.get("/cases").json()["cases"][0]
    assert summary["provenance"] == "internal"
    assert summary["is_synthetic"] is False

    detail = api.get(f"/cases/{case_id}").json()
    assert detail["is_synthetic"] is False
    prov = detail["provenance_detail"]
    # All four provenance facets are present.
    assert set(prov) == {"case", "transaction", "recovery_action", "payment_verification"}
    assert prov["case"]["synthetic"] is False


def test_synthetic_source_case_is_labelled_synthetic(api: TestClient):
    resp = api.post("/events", json=_synthetic_event())
    assert resp.status_code == 201
    case_id = resp.json()["cases"][0]["case_id"]
    assert resp.json()["cases"][0]["is_synthetic"] is True
    assert resp.json()["cases"][0]["provenance"] == "synthetic"

    detail = api.get(f"/cases/{case_id}").json()
    assert detail["is_synthetic"] is True
    prov = detail["provenance_detail"]
    assert prov["case"]["synthetic"] is True
    assert prov["transaction"]["synthetic"] is True
    # No action has run yet, so the recovery-action facet is not a synthetic claim.
    assert prov["recovery_action"]["label"] == "None yet"


def test_run_synthetic_case_marks_action_and_verification_provenance(api: TestClient):
    # After a mock run, the recovery action + verification are correctly described as simulated.
    case_id = api.post("/events", json=_synthetic_event()).json()["cases"][0]["case_id"]
    api.post(f"/cases/{case_id}/run")

    prov = api.get(f"/cases/{case_id}").json()["provenance_detail"]
    assert prov["recovery_action"]["synthetic"] is True  # MockAdapter → simulated
    assert prov["payment_verification"]["synthetic"] is True  # MockVerifier → simulated


# -- run (recovery workflow through the real PolicyEngine) ------------------------------


def test_run_case_recovers(api: TestClient):
    case_id = _create_subscription_case(api)
    resp = api.post(f"/cases/{case_id}/run")
    assert resp.status_code == 200
    body = resp.json()
    assert body["recovered"] is True
    assert body["amount_recovered"] == "1500.00"
    assert body["case"]["status"] == "recovered"

    # Persisted state reflects the recovery.
    assert api.get(f"/cases/{case_id}").json()["status"] == "recovered"


def test_run_high_value_case_escalates_not_bypassing_policy(api: TestClient):
    # A high-value overdue case must be ESCALATED by the PolicyEngine, never auto-executed,
    # proving the API does not bypass the gate.
    resp = api.post("/events", json=_overdue_event())
    case_id = resp.json()["cases"][0]["case_id"]

    run = api.post(f"/cases/{case_id}/run").json()
    assert run["recovered"] is False
    assert run["case"]["status"] == "escalated"
    assert run["case"]["escalation_reason"]


def test_audit_trail_records_pipeline_stages(api: TestClient):
    case_id = _create_subscription_case(api)
    api.post(f"/cases/{case_id}/run")

    entries = api.get(f"/cases/{case_id}/audit").json()["entries"]
    stages = [e["stage"] for e in entries]
    assert "case_created" in stages
    assert "diagnosis" in stages
    assert "policy_decision" in stages
    assert "execution" in stages
    assert "verification" in stages
    assert "recovery_result" in stages


# -- mode / status (Phase 15) -----------------------------------------------------------


def test_status_reports_production_mode_by_default(api: TestClient):
    body = api.get("/status").json()
    assert body["mode"] == "production"
    assert body["demo"] is False
    # No creds configured in the test settings → the live run path is not ready.
    assert body["run_ready"] is False


def test_status_reports_demo_mode(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'demo.db'}")
    db.create_all()
    app = create_api_app(Settings(_env_file=None, mode="demo"), database=db)
    with TestClient(app) as client:
        body = client.get("/status").json()
    db.dispose()
    assert body["mode"] == "demo" and body["demo"] is True
    assert body["ai_provider"] == "mock"
    assert body["run_ready"] is True


def test_demo_mode_run_waits_then_recovers_on_confirmation(tmp_path):
    # Demo mode wires the real PolicyEngine to offline mocks — no creds, no override needed.
    # Honest flow: running executes the action but recovery stays PENDING (creating a link/order
    # is NOT recovery); only confirming the simulated payment moves the case to RECOVERED.
    db = Database(f"sqlite:///{tmp_path / 'demorun.db'}")
    db.create_all()
    app = create_api_app(Settings(_env_file=None, mode="demo"), database=db)
    with TestClient(app) as client:
        case_id = client.post("/events", json=_subscription_event()).json()["cases"][0][
            "case_id"
        ]
        run = client.post(f"/cases/{case_id}/run").json()
        assert run["recovered"] is False
        assert run["case"]["status"] == "waiting"
        assert run["case"]["amount_recovered"] == "0"

        confirmed = client.post(f"/cases/{case_id}/simulate_payment").json()
    db.dispose()
    assert confirmed["recovered"] is True
    assert confirmed["case"]["status"] == "recovered"
    assert confirmed["case"]["amount_recovered"] == "1500.00"


def test_demo_mode_mark_unpaid_drives_retry_then_terminal_zero(tmp_path):
    # The honest "customer did not pay" path: running executes an intervention (WAITING), then
    # marking it unpaid records a verified NOT_RECOVERED and lets the existing bounded-retry
    # logic run further attempts until it terminates (STOP/ESCALATE) with ₹0 recovered.
    db = Database(f"sqlite:///{tmp_path / 'demounpaid.db'}")
    db.create_all()
    app = create_api_app(Settings(_env_file=None, mode="demo"), database=db)
    with TestClient(app) as client:
        case_id = client.post("/events", json=_subscription_event()).json()["cases"][0][
            "case_id"
        ]
        assert client.post(f"/cases/{case_id}/run").json()["case"]["status"] == "waiting"

        # Drive "not paid" until the case reaches a terminal outcome (bounded — never loops).
        terminal = None
        for _ in range(6):
            body = client.post(f"/cases/{case_id}/mark_unpaid").json()
            status = body["case"]["status"]
            assert body["recovered"] is False
            assert body["case"]["amount_recovered"] == "0"
            if status in {"escalated", "stopped"}:
                terminal = status
                break
        metrics = client.get("/metrics").json()
    db.dispose()
    assert terminal in {"escalated", "stopped"}, "unpaid retries must exhaust to STOP/ESCALATE"
    # An unpaid case contributes ₹0 recovered regardless of how many attempts were made.
    assert metrics["recovered"] == 0
    assert metrics["recovered_amount"] == "0"


def test_reset_clears_cases_events_and_audit(api: TestClient):
    # The dashboard "Reset" control wipes cases, events, and audit history so a reviewer can run
    # from scratch — and detection must not resurrect the old cases from leftover events.
    case_id = _create_subscription_case(api)
    api.post(f"/cases/{case_id}/run")
    assert api.get("/cases").json()["count"] == 1

    body = api.post("/reset").json()
    assert body["cases"] >= 1 and body["events"] >= 1 and body["audit"] >= 1

    assert api.get("/cases").json()["count"] == 0
    assert api.get("/metrics").json()["total_cases"] == 0
    # Re-posting is a clean slate (a fresh event creates exactly one new case).
    new_id = _create_subscription_case(api)
    assert api.get("/cases").json()["count"] == 1
    assert new_id


def test_production_disallows_demo_payment_shortcuts(api: TestClient):
    # Neither demo test double may fabricate a production recovery — both are demo-only (409).
    case_id = _create_subscription_case(api)
    assert api.post(f"/cases/{case_id}/simulate_payment").status_code == 409
    assert api.post(f"/cases/{case_id}/mark_unpaid").status_code == 409


def test_recheck_payment_recovers_from_verified_provider_status(tmp_path):
    # Production re-poll endpoint: recovery is confirmed from the verifier's paid status (the
    # polling transport the RazorpayVerifier supports), not fabricated by any frontend action.
    # It uses the AI-independent verification agent, so recheck works even when the AI provider
    # is not configured — confirming a real payment must never depend on the AI.
    db = Database(f"sqlite:///{tmp_path / 'recheck.db'}")
    db.create_all()
    app = create_api_app(Settings(_env_file=None), database=db)  # production, no AI configured

    # The run agent stages the case to WAITING (PENDING verify); the verification agent then
    # reports the payment as paid on the re-poll.
    run_agent = RecoveryAgent(
        db,
        diagnoser=MockDiagnoser(),
        adapter=MockAdapter(),
        verifier=MockVerifier(default=VerificationStatus.PENDING),
    )
    verify_agent = RecoveryAgent(
        db,
        diagnoser=MockDiagnoser(),
        adapter=MockAdapter(),
        verifier=MockVerifier(default=VerificationStatus.RECOVERED),
    )
    app.dependency_overrides[get_run_agent] = lambda: run_agent
    app.dependency_overrides[get_ingest_agent] = lambda: run_agent
    app.dependency_overrides[get_verify_agent] = lambda: verify_agent
    with TestClient(app) as client:
        case_id = client.post("/events", json=_subscription_event()).json()["cases"][0][
            "case_id"
        ]
        assert client.post(f"/cases/{case_id}/run").json()["case"]["status"] == "waiting"
        rechecked = client.post(f"/cases/{case_id}/recheck_payment").json()
    db.dispose()
    assert rechecked["recovered"] is True
    assert rechecked["case"]["status"] == "recovered"
    assert rechecked["case"]["amount_recovered"] == "1500.00"


# -- metrics ----------------------------------------------------------------------------


def test_metrics_reflect_recovered_case(api: TestClient):
    case_id = _create_subscription_case(api)
    api.post(f"/cases/{case_id}/run")

    metrics = api.get("/metrics").json()
    assert metrics["total_cases"] == 1
    assert metrics["recovered"] == 1
    assert metrics["recovered_amount"] == "1500.00"
    assert metrics["revenue_at_risk"] == "1500.00"


# -- analytics (action effectiveness, outcomes, timeline) -------------------------------


def test_analytics_empty_is_all_zero(api: TestClient):
    a = api.get("/analytics").json()
    assert a["total_cases"] == 0
    assert a["by_action"] == []
    assert a["timeline"] == []
    assert a["outcomes"]["recovered"] == 0


def test_analytics_reports_action_effectiveness_and_outcomes(api: TestClient):
    case_id = _create_subscription_case(api)
    api.post(f"/cases/{case_id}/run")

    a = api.get("/analytics").json()
    assert a["total_cases"] == 1
    assert a["outcomes"]["recovered"] == 1
    assert a["total_recovered_amount"] == "1500.00"
    # The executed intervention is attributed the recovery (not just a case count).
    assert a["by_action"], "expected at least one action row"
    top = a["by_action"][0]
    assert top["attempts"] >= 1
    assert top["recoveries"] == 1
    assert top["recovered_amount"] == "1500.00"
    assert top["recovery_rate"] == "1.0000"
    # A time bucket exists for the recovery activity.
    assert a["timeline"] and a["timeline"][-1]["recovered_count"] >= 1


def test_analytics_high_value_escalation_has_no_recovery(api: TestClient):
    case_id = api.post("/events", json=_overdue_event()).json()["cases"][0]["case_id"]
    api.post(f"/cases/{case_id}/run")

    a = api.get("/analytics").json()
    assert a["outcomes"]["escalated"] == 1
    assert a["outcomes"]["recovered"] == 0
    assert a["total_recovered_amount"] == "0"


# -- consent / do-not-contact surfacing -------------------------------------------------


def test_case_detail_exposes_consent_state(api: TestClient):
    case_id = _create_subscription_case(api)
    detail = api.get(f"/cases/{case_id}").json()
    # Default consent is present; nothing is blocked.
    assert detail["do_not_contact"] is False
    assert detail["blocked_contact_actions"] == []


def test_case_detail_lists_blocked_contact_actions_when_dnd(tmp_path, make_case):
    db = Database(f"sqlite:///{tmp_path / 'dnd.db'}")
    db.create_all()
    case = make_case(do_not_contact=True)
    with db.session() as s:
        CaseRepository(s).add(case)
    app = create_api_app(Settings(_env_file=None), database=db)
    with TestClient(app) as client:
        detail = client.get(f"/cases/{case.case_id}").json()
    db.dispose()
    assert detail["do_not_contact"] is True
    assert detail["blocked_contact_actions"] == ["create_payment_link", "send_reminder"]


# -- evaluation (reuses the Phase 8 offline harness) ------------------------------------


def test_evaluation_returns_baseline_vs_revguard(api: TestClient):
    body = api.get("/evaluation?seed=7").json()
    assert body["seed"] == 7
    assert "baseline" in body["comparison"]
    assert "revguard" in body["comparison"]
    assert "delta" in body["comparison"]
    # The comparison is over the seeded synthetic batch, independent of stored demo cases.
    assert body["comparison"]["revguard"]["total_cases"] > 0


# -- webhook (reuses Phase 9 verification; never trusts client success) -----------------


def _paid_webhook_body() -> bytes:
    return json.dumps(
        {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {"entity": {"id": "plink_1", "status": "paid",
                                            "amount": 150000, "currency": "INR"}},
                "payment": {"entity": {"id": "pay_1", "status": "captured",
                                       "order_id": "order_1", "amount": 150000,
                                       "currency": "INR"}},
            },
        }
    ).encode()


def _sign(raw: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_webhook_accepts_valid_signature(api: TestClient):
    raw = _paid_webhook_body()
    resp = api.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": _sign(raw)}
    )
    assert resp.status_code == 200
    assert resp.json()["recovered"] is True
    assert resp.json()["event_type"] == "payment_succeeded"


def test_webhook_rejects_bad_signature(api: TestClient):
    raw = _paid_webhook_body()
    resp = api.post(
        "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": "deadbeef"}
    )
    assert resp.status_code == 400


def _paid_webhook_for_case(case_id: str, amount_paise: int = 150000) -> bytes:
    return json.dumps(
        {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_r",
                        "status": "paid",
                        "amount": amount_paise,
                        "currency": "INR",
                        "notes": {"case_id": case_id, "action": "create_payment_link"},
                    }
                },
                "payment": {
                    "entity": {"id": "pay_r", "status": "captured", "amount": amount_paise,
                               "currency": "INR"}
                },
            },
        }
    ).encode()


def test_webhook_reconciles_paid_event_to_waiting_case(tmp_path, make_case):
    # End-to-end: a signature-verified paid webhook confirms recovery on the matching case
    # (which was left WAITING after execution). No creds needed — reconciliation is DB-only.
    db = Database(f"sqlite:///{tmp_path / 'recon.db'}")
    db.create_all()
    case = make_case(status=CaseStatus.WAITING)
    with db.session() as s:
        CaseRepository(s).add(case)
    app = create_api_app(
        Settings(_env_file=None, razorpay_webhook_secret=_WEBHOOK_SECRET), database=db
    )
    with TestClient(app) as client:
        raw = _paid_webhook_for_case(case.case_id)
        resp = client.post(
            "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": _sign(raw)}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["reconciled"] is True
        assert body["matched_case_id"] == case.case_id
        # The case is now verified-recovered (never from client success — from the paid status).
        assert client.get(f"/cases/{case.case_id}").json()["status"] == "recovered"
    db.dispose()


def test_webhook_unconfigured_secret_is_503(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'nosecret.db'}")
    db.create_all()
    app = create_api_app(Settings(_env_file=None), database=db)  # no webhook secret
    with TestClient(app) as client:
        raw = _paid_webhook_body()
        resp = client.post(
            "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": _sign(raw)}
        )
    db.dispose()
    assert resp.status_code == 503


def test_run_returns_503_when_integration_unconfigured(tmp_path):
    # Without the dependency override, the real run agent build fails closed (no creds/SDK).
    db = Database(f"sqlite:///{tmp_path / 'unconfigured.db'}")
    db.create_all()
    app = create_api_app(Settings(_env_file=None), database=db)
    app.dependency_overrides[get_ingest_agent] = lambda: RecoveryAgent(
        db, diagnoser=MockDiagnoser()
    )
    with TestClient(app) as client:
        case_id = client.post("/events", json=_subscription_event()).json()["cases"][0][
            "case_id"
        ]
        resp = client.post(f"/cases/{case_id}/run")
    db.dispose()
    assert resp.status_code == 503


# -- input validation at the API boundary (regression) ----------------------------------


@pytest.mark.parametrize("amount", ["-5.00", "0", "0.00"])
def test_post_event_rejects_non_positive_amount(api: TestClient, amount: str):
    # Non-positive money must be a clean 422 at the schema boundary, never a 500 from the
    # domain Event rejecting it after EventIn validation passed.
    bad = _subscription_event(amount=amount)
    assert api.post("/events", json=bad).status_code == 422


def test_post_event_rejects_non_finite_amount(api: TestClient):
    bad = _subscription_event(amount="1e400")  # overflows to Infinity
    assert api.post("/events", json=bad).status_code == 422


# -- payment-degradation cases are deduplicated (regression) ----------------------------


def test_payment_degradation_does_not_spawn_duplicate_cases(api: TestClient):
    # A method-wide degradation carries no entity id; repeated detections must update ONE case
    # (keyed on method+currency), never a new case per event — otherwise the same at-risk money
    # is counted many times in the metrics.
    for i in range(24):
        api.post(
            "/events",
            json={
                "event_type": "payment_failed",
                "source": "internal",
                "payment_id": f"pay_{i}",
                "amount": "500.00",
                "currency": "INR",
                "metadata": {"method": "upi", "error_code": "gateway_timeout"},
            },
        )
    cases = api.get("/cases").json()["cases"]
    degradation = [c for c in cases if c["case_type"] == "payment_degradation"]
    assert len(degradation) == 1
    assert degradation[0]["case_id"] == "case_payment_degradation_method_upi_INR"

    metrics = api.get("/metrics").json()
    # revenue at risk equals the single case's amount (no multi-counting of the same money).
    assert metrics["revenue_at_risk"] == degradation[0]["amount_at_risk"]
