# RevGuard — Product Specification

> Status: Draft for approval. Reflects the approved decisions only. No requirements
> beyond those stated have been invented.

## 1. Summary

RevGuard is an AI-powered **revenue recovery agent** built for the Razorpay Revenue
Recovery / AI Agent problem statement. It detects revenue at risk, diagnoses the
context using an AI reasoning component, and runs a **bounded, policy-controlled**
recovery workflow that can execute permitted actions, escalate to a human, or stop.

The defining constraint: **the AI never executes external side effects**. The AI is a
reasoning/recommendation component only. All permissions and side effects are governed
by a deterministic policy engine and executed by a deterministic executor.

## 2. Goals

1. Detect revenue at risk across four scenarios (A–D below).
2. Diagnose the reason/context of each case.
3. Recommend an appropriate recovery intervention (AI).
4. Gate every recommendation through a deterministic policy engine.
5. Execute only permitted actions.
6. Support compliant human escalation.
7. Enforce stopping rules.
8. Verify whether money was actually recovered.
9. Maintain a complete audit trail.
10. Measure money recovered across a batch of cases, and compare against a baseline.

## 3. Revenue-risk workflows (all required)

| ID | Workflow | Detection | Recovery intent |
|----|----------|-----------|-----------------|
| A | **Payment degradation** | Abnormal payment-performance degradation; identify affected method/root cause where possible | Determine appropriate intervention; **analytics/root-cause focus** in UI |
| B | **Failed subscription / mandate recovery** | Failed recurring/mandate payments | Bounded sequence: retry → payment-link → reminder → escalation → stop |
| C | **Checkout abandonment** | Abandoned checkouts | Decide if recovery is appropriate; execute bounded recovery action |
| D | **B2B overdue receivables** | Overdue invoices | Intervention + **promise-to-pay** tracking; escalate when required |

**Demo priority (polished end-to-end):** B → C → D. **A is still fully implemented and
evaluated**, but its UI is a focused analytics/root-cause view rather than the primary
recovery demo.

## 4. Deliverables

- FastAPI backend (modular monolith).
- Web dashboard (polished, working).
- CLI for synthetic data generation and batch evaluation.
- SQLite persistence (initially).
- AI agent behind a provider abstraction (offline-capable).
- Deterministic policy engine (the single execution gate).
- Mock execution adapter.
- Razorpay Test Mode adapter (later phase).
- Webhook endpoint + signature verification (later phase).
- Audit trail.
- Batch evaluation + metrics with baseline vs RevGuard comparison.

## 5. Non-negotiable safety architecture

```
Event → Detector → RecoveryCase → AI diagnosis/recommendation → PolicyEngine
→ APPROVE / ESCALATE / STOP → Executor → Outcome verification → RecoveryResult
→ AuditLog → Metrics
```

- The **policy engine is deterministic** and is the **only** gate before execution.
- The **executor rejects any action not carrying an APPROVE decision.**
- The LLM cannot import or call executors, the database, or the Razorpay SDK.

Enforced controls (details in POLICY_SPEC.md): max retry attempts, action cooldowns,
idempotency, amount thresholds, stopping rules, customer opt-out/do-not-contact,
escalation thresholds, case expiration, duplicate-event protection, already-recovered
protection.

## 6. Human escalation

An in-application escalation **queue** with persistent records containing at minimum:
case ID, reason, amount at risk, customer, AI recommendation, policy decision,
escalation timestamp, current status. A human can mark an escalation **resolved**.
No email/Slack unless time remains after the core product is complete.

## 7. Recovery verification

- **Primary:** webhook-driven verification.
- **Fallback:** a polling / payment-status verification abstraction where appropriate.
- The core recovery engine must **not** depend on a public webhook URL during
  development.

## 8. Baseline vs RevGuard

- **Baseline:** a deterministic, rule-only recovery strategy.
- **RevGuard:** detection + contextual AI diagnosis + policy engine + bounded workflow.
- The evaluator compares both on the **same input batch**. No claim that AI is better is
  made unless the evaluator actually produces the comparison.

## 9. Constraints & non-goals

- Modular monolith. **No** microservices, Kubernetes, queues, or distributed systems.
- Must run **completely offline** using `MockDiagnoser` (no Anthropic key required).
- Do not invent Razorpay API fields/behavior; consult official docs at integration time.
- Optimize for correctness, demonstrability, testability, clear architecture, reliable
  demo behavior.

## 10. Environment notes

- Target Python **>=3.12,<3.14**. **3.12 is not currently installed on the build
  machine (only 3.14.2 present).** No system changes will be made automatically.
- Node available for frontend tooling if a separate frontend is chosen.
