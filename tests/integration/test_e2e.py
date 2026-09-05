"""Phase 12 — full end-to-end validation of the production/demo path.

Each test drives the **real** production wiring end to end:

    Event -> Detection -> Case -> AnthropicProvider (real class) -> PolicyEngine (real)
    -> ActionExecutor -> RazorpayTestAdapter (real) -> RazorpayVerifier (real)
    -> verified webhook -> reconcile -> RecoveryCase -> Audit -> Metrics

Only the two *external transports* are mocked: the Anthropic HTTP client (a fake that returns
tool-use blocks, exercising the real prompt/parse/validate path) and the Razorpay HTTP client
(a stateful fake mimicking Test Mode order/payment-link create + fetch). No network, no real
credentials, no real payment. The PolicyEngine is always the real one and is never bypassed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from revguard.api.app import create_api_app  # noqa: E402
from revguard.api.dependencies import get_ingest_agent, get_run_agent  # noqa: E402
from revguard.config import Settings  # noqa: E402
from revguard.diagnosis import AnthropicProvider, MockDiagnoser, create_diagnoser  # noqa: E402
from revguard.diagnosis.prompts import SUBMIT_TOOL_NAME  # noqa: E402
from revguard.domain import ActionType  # noqa: E402
from revguard.integrations.razorpay import build_integrated_agent  # noqa: E402
from revguard.integrations.razorpay.adapter import RazorpayTestAdapter  # noqa: E402
from revguard.integrations.razorpay.verifier import RazorpayVerifier  # noqa: E402
from revguard.orchestrator import RecoveryAgent  # noqa: E402
from revguard.persistence import Database  # noqa: E402
from revguard.policy import PolicyConfig  # noqa: E402

_WEBHOOK_SECRET = "whsec_e2e"


# =====================================================================================
# Fakes for the two external transports (and nothing else)
# =====================================================================================


class _FakeAnthropicMessages:
    def __init__(self, script):
        self._script = script
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._script(kwargs) if callable(self._script) else self._script
        if isinstance(result, Exception):
            raise result
        return result


class FakeAnthropic:
    """Minimal stand-in for anthropic.Anthropic — only messages.create is used."""

    def __init__(self, script):
        self.messages = _FakeAnthropicMessages(script)


def _tool_response(action: ActionType, *, confidence: float = 0.85, rationale: str = "recover"):
    """A well-formed Messages response with a submit_action_proposal tool_use block."""
    return {
        "content": [
            {
                "type": "tool_use",
                "name": SUBMIT_TOOL_NAME,
                "input": {
                    "action_type": action.value,
                    "rationale": rationale,
                    "confidence": confidence,
                    "parameters": {},
                },
            }
        ]
    }


class _FakeRazorpayResource:
    def __init__(self, kind: str, store: dict):
        self.kind = kind
        self.store = store
        self.create_calls: list = []
        self.fetch_calls: list = []
        self.force_paid = False
        self.raise_on_create: Exception | None = None

    def create(self, *args, **kwargs):
        payload = kwargs.get("data") if "data" in kwargs else (args[0] if args else {})
        self.create_calls.append(payload)
        if self.raise_on_create is not None:
            raise self.raise_on_create
        oid = f"{self.kind}_{len(self.create_calls)}"
        entity = {
            "id": oid,
            "status": "created",
            "amount": payload.get("amount"),
            "currency": payload.get("currency"),
            "notes": payload.get("notes") or {},
        }
        self.store[oid] = entity
        return entity

    def fetch(self, reference):
        self.fetch_calls.append(reference)
        entity = dict(self.store.get(reference, {"id": reference, "status": "created"}))
        if self.force_paid:
            entity["status"] = "paid"
            entity["amount_paid"] = entity.get("amount")
        return entity


class FakeRazorpay:
    """Stateful stand-in for a Razorpay Test Mode client (order + payment_link)."""

    def __init__(self):
        self.store: dict = {}
        self.order = _FakeRazorpayResource("order", self.store)
        self.payment_link = _FakeRazorpayResource("plink", self.store)


# =====================================================================================
# Harness: an app wired with the REAL components + the two fakes
# =====================================================================================


class Harness:
    def __init__(self, tmp_path, *, anthropic_script, razorpay=None, policy=None):
        self.db = Database(f"sqlite:///{tmp_path / 'e2e.db'}")
        self.db.create_all()
        self.razorpay = razorpay or FakeRazorpay()
        settings = Settings(_env_file=None, razorpay_webhook_secret=_WEBHOOK_SECRET)
        self.app = create_api_app(settings, database=self.db)

        # The run agent is the real production stack with only the transports faked.
        diagnoser = AnthropicProvider(
            model="claude-test", api_key="sk-test-not-used", client=FakeAnthropic(anthropic_script)
        )
        self.run_agent = RecoveryAgent(
            self.db,
            diagnoser=diagnoser,
            adapter=RazorpayTestAdapter(self.razorpay),
            verifier=RazorpayVerifier(self.razorpay),
            config=policy,
        )
        self.app.dependency_overrides[get_run_agent] = lambda: self.run_agent
        self.app.dependency_overrides[get_ingest_agent] = lambda: RecoveryAgent(self.db)
        self.client = TestClient(self.app)

    def close(self):
        self.client.close()
        self.db.dispose()

    # -- API helpers --------------------------------------------------------------------

    def post_event(self, event: dict) -> dict:
        resp = self.client.post("/events", json=event)
        assert resp.status_code == 201, resp.text
        return resp.json()

    def create_case(self, event: dict, expected_type: str | None = None) -> str:
        # POST /events returns every affected case (detection re-emits prior signals as
        # refreshed), so select the one matching the workflow under test when given.
        cases = self.post_event(event)["cases"]
        assert cases, f"detector raised no case for {event['event_type']}"
        if expected_type is not None:
            match = [c for c in cases if c["case_type"] == expected_type]
            assert match, f"no {expected_type} case in {[c['case_type'] for c in cases]}"
            return match[0]["case_id"]
        return cases[0]["case_id"]

    def run(self, case_id: str) -> dict:
        resp = self.client.post(f"/cases/{case_id}/run")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def case(self, case_id: str) -> dict:
        return self.client.get(f"/cases/{case_id}").json()

    def audit_stages(self, case_id: str) -> list[str]:
        return [e["stage"] for e in self.client.get(f"/cases/{case_id}/audit").json()["entries"]]

    def metrics(self) -> dict:
        return self.client.get("/metrics").json()

    def webhook(self, raw: bytes, *, signature: str | None = None) -> dict:
        sig = signature if signature is not None else _sign(raw)
        return self.client.post(
            "/webhooks/razorpay", content=raw, headers={"X-Razorpay-Signature": sig}
        )


def _sign(raw: bytes, secret: str = _WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _paid_webhook(case_id: str, *, amount: str) -> bytes:
    paise = int((Decimal(amount) * 100).to_integral_value())
    return json.dumps(
        {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {
                        "id": "plink_wh",
                        "status": "paid",
                        "amount": paise,
                        "currency": "INR",
                        "notes": {"case_id": case_id},
                    }
                },
                "payment": {
                    "entity": {"id": "pay_wh", "status": "captured", "amount": paise,
                               "currency": "INR"}
                },
            },
        }
    ).encode()


# -- event factories (each reliably fires its detector) --------------------------------


def _subscription_event(amount: str = "1500.00") -> dict:
    return {
        "event_type": "subscription_payment_failed",
        "source": "internal",
        "customer_id": "cust_sub",
        "subscription_id": "sub_1",
        "amount": amount,
        "currency": "INR",
        "metadata": {"failure_reason": "card_declined"},
    }


def _checkout_event(amount: str = "6000.00") -> dict:
    return {
        "event_type": "checkout_abandoned",
        "source": "internal",
        "customer_id": "cust_chk",
        "order_id": "ord_1",
        "amount": amount,
        "currency": "INR",
        "metadata": {"abandonment_stage": "payment"},
    }


def _overdue_event(amount: str = "8000.00", days: int = 65) -> dict:
    return {
        "event_type": "invoice_overdue",
        "source": "internal",
        "customer_id": "cust_inv",
        "invoice_id": "inv_1",
        "amount": amount,
        "currency": "INR",
        "metadata": {"days_overdue": days},
    }


def _degradation_events() -> list[dict]:
    method = "card_x"
    failed = [
        {
            "event_type": "payment_failed",
            "source": "internal",
            "payment_id": f"pay_f_{i}",
            "amount": "800.00",
            "currency": "INR",
            "metadata": {"method": method},
        }
        for i in range(16)
    ]
    ok = [
        {
            "event_type": "payment_succeeded",
            "source": "internal",
            "payment_id": f"pay_s_{i}",
            "amount": "800.00",
            "currency": "INR",
            "metadata": {"method": method},
        }
        for i in range(4)
    ]
    return failed + ok


# =====================================================================================
# 1. Successful recovery — via polling (verifier fetches a paid status)
# =====================================================================================


def test_successful_recovery_via_polling(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.RETRY_PAYMENT))
    h.razorpay.order.force_paid = True  # Razorpay reports the order paid on fetch
    try:
        case_id = h.create_case(_subscription_event())
        run = h.run(case_id)
        assert run["recovered"] is True
        assert run["case"]["status"] == "recovered"
        assert run["amount_recovered"] == "1500.00"

        stages = h.audit_stages(case_id)
        for expected in ("case_created", "diagnosis", "policy_decision", "execution",
                         "verification", "recovery_result"):
            assert expected in stages
        m = h.metrics()
        assert m["recovered"] == 1 and m["recovered_amount"] == "1500.00"
    finally:
        h.close()


# =====================================================================================
# 2. Successful recovery — via a verified webhook (create -> WAITING -> paid webhook)
# =====================================================================================


def test_successful_recovery_via_webhook(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = h.create_case(_checkout_event(amount="6000.00"))
        run = h.run(case_id)
        # Link created, not yet paid -> pending -> WAITING (NOT recovered on API success).
        assert run["recovered"] is False
        assert run["case"]["status"] == "waiting"
        assert h.razorpay.payment_link.create_calls, "expected a Razorpay Test Mode link create"

        resp = h.webhook(_paid_webhook(case_id, amount="6000.00"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["reconciled"] is True and body["matched_case_id"] == case_id

        assert h.case(case_id)["status"] == "recovered"
        assert h.metrics()["recovered_amount"] == "6000.00"
    finally:
        h.close()


# =====================================================================================
# 3. Pending verification — created but unpaid stays WAITING, nothing recovered
# =====================================================================================


def test_pending_verification_waits(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = h.create_case(_checkout_event())
        run = h.run(case_id)
        assert run["case"]["status"] == "waiting"
        assert Decimal(run["amount_recovered"]) == 0
        assert h.metrics()["recovered"] == 0
    finally:
        h.close()


# =====================================================================================
# 4. Policy gate — APPROVE vs ESCALATE vs STOP
# =====================================================================================


def test_policy_escalates_high_value_without_executing(tmp_path):
    # A high-value overdue case (> escalation threshold) must ESCALATE — the AI's recommendation
    # to act is overridden by the deterministic gate, and nothing is executed on Razorpay.
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = h.create_case(_overdue_event(amount="90000.00"))
        run = h.run(case_id)
        assert run["case"]["status"] == "escalated"
        assert run["case"]["escalation_reason"]
        assert not h.razorpay.payment_link.create_calls  # gate blocked execution
        assert "policy_decision" in h.audit_stages(case_id)
    finally:
        h.close()


def test_policy_stops_after_maximum_attempts_on_failed_recovery(tmp_path):
    # Every Razorpay create fails -> execution FAILED -> NOT_RECOVERED -> retried until the
    # PolicyEngine STOPs at the attempt budget. Covers failed recovery + maximum attempts + STOP.
    razorpay = FakeRazorpay()
    razorpay.order.raise_on_create = RuntimeError("gateway error")
    # cooldown=0 so the identical retry is not stopped by the cooldown rule first — this lets
    # the attempt budget be the thing that stops the case (the point of this test).
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.RETRY_PAYMENT),
                razorpay=razorpay, policy=PolicyConfig(max_attempts=3, cooldown_seconds=0))
    try:
        case_id = h.create_case(_subscription_event())
        run = h.run(case_id)
        assert run["case"]["status"] == "stopped"
        assert run["case"]["stop_reason"] == "max_attempts_reached"
        assert Decimal(run["amount_recovered"]) == 0
        assert len(razorpay.order.create_calls) == 3  # exactly the attempt budget, no more
        assert h.metrics()["recovered"] == 0
    finally:
        h.close()


# =====================================================================================
# 5. Malformed / failed AI response degrades safely to ESCALATE (real AnthropicProvider)
# =====================================================================================


def test_malformed_ai_response_escalates(tmp_path):
    # The model returns no structured proposal -> AnthropicProvider fails closed to
    # RECOMMEND_ESCALATION -> PolicyEngine ESCALATEs. The AI can never reach the executor.
    h = Harness(tmp_path, anthropic_script={"content": []})
    try:
        case_id = h.create_case(_subscription_event())
        run = h.run(case_id)
        assert run["case"]["status"] == "escalated"
        assert not h.razorpay.order.create_calls
    finally:
        h.close()


def test_failed_ai_call_escalates(tmp_path):
    # A provider/transport error also fails closed to escalation (no crash, no execution).
    h = Harness(tmp_path, anthropic_script=RuntimeError("provider timeout"))
    try:
        case_id = h.create_case(_subscription_event())
        assert h.run(case_id)["case"]["status"] == "escalated"
        assert not h.razorpay.order.create_calls
    finally:
        h.close()


# =====================================================================================
# 6. Partial recovery and the amount-at-risk cap (through the webhook path)
# =====================================================================================


def _to_waiting(h: Harness, event: dict) -> str:
    case_id = h.create_case(event)
    assert h.run(case_id)["case"]["status"] == "waiting"
    return case_id


def test_partial_recovery_records_partial_amount(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = _to_waiting(h, _checkout_event(amount="6000.00"))
        h.webhook(_paid_webhook(case_id, amount="2500.00"))  # customer paid part
        detail = h.case(case_id)
        assert detail["status"] == "recovered"
        assert detail["amount_recovered"] == "2500.00"
    finally:
        h.close()


def test_overpayment_is_capped_at_amount_at_risk(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = _to_waiting(h, _checkout_event(amount="6000.00"))
        h.webhook(_paid_webhook(case_id, amount="9999.00"))  # provider reports more
        assert h.case(case_id)["amount_recovered"] == "6000.00"  # capped
    finally:
        h.close()


# =====================================================================================
# 7. Idempotency — duplicate events, duplicate webhooks, duplicate execution
# =====================================================================================


def test_duplicate_events_create_only_one_case(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.RETRY_PAYMENT))
    try:
        h.post_event(_subscription_event())
        h.post_event(_subscription_event())  # same subject -> deterministic case id
        listed = h.client.get("/cases").json()
        assert listed["count"] == 1
    finally:
        h.close()


def test_duplicate_webhook_is_idempotent_no_double_count(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = _to_waiting(h, _checkout_event(amount="6000.00"))
        raw = _paid_webhook(case_id, amount="6000.00")
        first = h.webhook(raw).json()
        second = h.webhook(raw).json()  # replay
        assert first["reconciled"] is True
        assert second["reconciled"] is False  # already recovered -> no-op
        assert h.case(case_id)["amount_recovered"] == "6000.00"  # not doubled
        assert h.metrics()["recovered"] == 1
    finally:
        h.close()


def test_running_a_waiting_case_again_does_not_re_execute(tmp_path):
    # Duplicate execution guard: re-running a WAITING case performs no new Razorpay call.
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = _to_waiting(h, _checkout_event())
        calls_after_first = len(h.razorpay.payment_link.create_calls)
        h.run(case_id)  # run again while WAITING
        assert len(h.razorpay.payment_link.create_calls) == calls_after_first
        assert h.case(case_id)["status"] == "waiting"
    finally:
        h.close()


def test_already_recovered_case_run_is_noop(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.RETRY_PAYMENT))
    h.razorpay.order.force_paid = True
    try:
        case_id = h.create_case(_subscription_event())
        assert h.run(case_id)["recovered"] is True
        calls = len(h.razorpay.order.create_calls)
        again = h.run(case_id)  # terminal -> no-op
        assert again["case"]["status"] == "recovered"
        assert len(h.razorpay.order.create_calls) == calls  # nothing re-executed
    finally:
        h.close()


def test_bad_webhook_signature_never_reconciles(tmp_path):
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        case_id = _to_waiting(h, _checkout_event(amount="6000.00"))
        resp = h.webhook(_paid_webhook(case_id, amount="6000.00"), signature="deadbeef")
        assert resp.status_code == 400
        assert h.case(case_id)["status"] == "waiting"  # untouched
    finally:
        h.close()


# =====================================================================================
# 8. All four workflows reach execution/verification through the real stack
# =====================================================================================


def test_all_four_workflows_flow_through_the_pipeline(tmp_path):
    # subscription + checkout + overdue each execute a payment action and reach WAITING/RECOVERED;
    # payment-degradation (a 20-event burst) is detected and diagnosed through the same stack.
    h = Harness(tmp_path, anthropic_script=_tool_response(ActionType.CREATE_PAYMENT_LINK))
    try:
        sub = h.create_case(_subscription_event(), expected_type="failed_subscription")
        chk = h.create_case(_checkout_event(), expected_type="checkout_abandonment")
        ovd = h.create_case(_overdue_event(amount="8000.00"),
                            expected_type="overdue_receivable")

        # Degradation needs the whole burst before the detector concludes.
        deg_id = None
        for ev in _degradation_events():
            for c in h.post_event(ev)["cases"]:
                if c["case_type"] == "payment_degradation":
                    deg_id = c["case_id"]
        assert deg_id, "payment degradation was not detected from the burst"

        seen = set()
        for cid in (sub, chk, ovd, deg_id):
            detail = h.case(cid)
            seen.add(detail["case_type"])
            h.run(cid)
            after = h.case(cid)["status"]
            assert after in {"waiting", "recovered", "escalated", "stopped"}
            assert "diagnosis" in h.audit_stages(cid)
            assert "policy_decision" in h.audit_stages(cid)

        assert seen == {
            "failed_subscription",
            "checkout_abandonment",
            "overdue_receivable",
            "payment_degradation",
        }
    finally:
        h.close()


# =====================================================================================
# 9. Production wiring really uses Anthropic + Razorpay Test Mode (not mocks)
# =====================================================================================


def test_integrated_agent_wires_anthropic_and_razorpay_test_mode(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'wire.db'}")
    db.create_all()
    settings = Settings(
        _env_file=None,
        ai_provider="anthropic",
        anthropic_api_key="sk-test",
        razorpay_key_id="rzp_test_abc",
        razorpay_key_secret="secret_value",
    )
    try:
        agent = build_integrated_agent(db, settings, client=FakeRazorpay())
        assert isinstance(agent._diagnoser, AnthropicProvider)
        assert isinstance(agent._adapter, RazorpayTestAdapter)
        assert isinstance(agent._verifier, RazorpayVerifier)
        assert not isinstance(agent._diagnoser, MockDiagnoser)
    finally:
        db.dispose()


def test_create_diagnoser_selects_provider_by_config():
    anthropic = create_diagnoser(
        Settings(_env_file=None, ai_provider="anthropic", anthropic_api_key="sk-test")
    )
    assert isinstance(anthropic, AnthropicProvider)
    # Default/offline stays on the mock (no credentials required).
    assert isinstance(create_diagnoser(Settings(_env_file=None)), MockDiagnoser)
