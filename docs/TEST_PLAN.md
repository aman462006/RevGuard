# RevGuard — Test Plan

> Status: Draft for approval. Every phase finishes with automated tests. Do not proceed to
> the next phase while the current phase has failing tests. Prefer deterministic unit +
> integration tests over relying on LLM behavior for business rules.

## 1. Principles

- **Gate per phase:** a phase is "done" only when its tests pass.
- **Deterministic first:** business rules (detection, policy, metrics, executor gating)
  are validated with deterministic tests, using `MockProvider` for AI.
- **No correctness dependency on live LLM output.**
- Tools: `pytest`. Layout: `tests/unit/` (per stage) and `tests/integration/`.

## 2. Per-phase test requirements

| Phase | Deliverable | Required tests |
|---|---|---|
| 0 — Scaffold | Project skeleton, config, tooling | `pytest` runs; trivial smoke test; config loads from env with defaults. |
| 1 — Domain models | Pydantic contracts + `ActionType` enum | Schema validation; invalid/unlisted action rejected; serialization round-trips. |
| 2 — Synthetic data + detectors | Generator + detectors A–D | Known inputs → expected `RevenueRiskSignal`; generator reproducible (seed); each workflow detectable. |
| 3 — Persistence + audit log | SQLite, repositories, append-only audit | Case CRUD; **audit is append-only**; every transition recorded with timestamps. |
| 4 — Policy engine | Deterministic gate | Truth tables for every control (§POLICY_SPEC 3); decision precedence; threshold/cooldown/expiration boundaries; property: no APPROVE-less execution possible. |
| 5 — AI diagnosis | MockDiagnoser then AnthropicProvider | Mock: deterministic proposals per workflow. Anthropic: schema-conformance + safe failure (timeout/invalid output → no executed action). Provider selworks via env. |
| 6 — Mock executor | ActionAdapter + MockAdapter | Executor **rejects** any non-APPROVE action; idempotency honored; outcome recorded. |
| 7 — Verification + orchestrator | Control loop | Full pipeline per workflow (mock); bounded iteration; stopping enforced; already-recovered halts actions. |
| 8 — Batch eval + metrics | Runner + report | Exact metric values on fixed fixtures; reproducibility (seed); baseline vs RevGuard both produced on one batch. |
| 9 — Razorpay Test Mode + webhooks | Adapter + webhook ingest | Signature verification (valid/invalid); idempotent event processing; adapter behavior against documented Razorpay behavior (mocked HTTP); no invented fields. |
| 10 — FastAPI endpoints | API surface | Endpoint contract tests; webhook route; error handling; auth/validation as applicable. |
| 11 — Dashboard | Web dashboard | Data-integration smoke tests; renders metrics/audit/escalation views against API. |
| 12 — End-to-end integration | Full system | Event → metrics happy paths for A–D; escalation and stop paths. |
| 13 — Adversarial/failure | Robustness | Duplicate events; already-recovered; do-not-contact; expired cases; AI proposing disallowed/unlisted actions is blocked; malformed AI output; provider outage. |
| 14 — Demo preparation | Demo scripts/data | Seeded demo batch reproducible; scripted flows pass. |

## 3. Critical invariants (asserted repeatedly)

1. No executed action ever occurs without a matching `APPROVE` decision.
2. The AI cannot cause side effects; it only returns `ActionProposal`.
3. The AI cannot propose an action outside the `ActionType` whitelist.
4. All enforced controls (retry cap, cooldown, idempotency, amount thresholds, stopping
   rules, do-not-contact, escalation thresholds, case expiration, duplicate-event
   protection, already-recovered protection) hold at their boundaries.
5. Audit log is append-only and complete for every processed case.
6. Evaluation is reproducible and produces the baseline vs RevGuard comparison.
7. The system runs fully offline with `MockProvider` and no Razorpay credentials.

## 4. Categories

- **Unit:** detectors, policy rules, metric math, schema validation, adapters.
- **Integration:** orchestrator pipeline, persistence + audit, evaluator, API routes.
- **Adversarial:** Phase 13 failure/abuse scenarios above.
