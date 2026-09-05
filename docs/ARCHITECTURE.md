# RevGuard — Architecture Specification

> Status: Draft for approval. Modular monolith. No microservices/queues/K8s.

## 1. Architectural principle

The LLM is a **pure reasoning/recommendation component**. It returns a typed
`ActionProposal` and nothing else — it performs no I/O and holds no references to
executors, the database, or payment SDKs. All permissions and side effects are owned by
deterministic Python. The **policy engine is the single gate** before any executor runs.

## 2. Pipeline (canonical, non-negotiable)

```
Event source (synthetic / Razorpay Test Mode)
  → 1. Detector            (deterministic)   → RevenueRiskSignal
  → 2. Case store                            → RecoveryCase
  → 3. AI diagnosis        (AIProvider)       → ActionProposal   (no side effects)
  → 4. Policy engine       (deterministic)    → PolicyDecision {APPROVE|ESCALATE|STOP}
  → 5. Executor            (adapter)          → ActionOutcome    (only if APPROVE)
  → 6. Verification        (webhook/polling)  → recovered? yes/no
  → loop back through policy until resolved / escalated / stopped
  → 7. RecoveryResult
  → 8. AuditLog            (append-only)
  → 9. Metrics
```

The orchestrator is a **deterministic control loop** that wires stages 3–6 and enforces
bounded iteration.

## 3. Module layout (modular monolith)

```
src/revguard/
  config.py            # pydantic-settings; env-driven configuration
  domain/              # typed contracts between stages (Pydantic)
    events.py signals.py cases.py proposals.py decisions.py results.py escalation.py
  detection/           # Stage 1 — deterministic detectors (A, B, C, D)
  diagnosis/           # Stage 3 — AIProvider abstraction + Mock/Anthropic + prompts
  policy/              # Stage 4 — engine, permissions, guardrails, stopping_rules
  execution/           # Stage 5 — ActionAdapter interface, mock + razorpay adapters
  verification/        # Stage 6 — webhook verifier + polling verifier (abstraction)
  orchestrator/        # deterministic control loop
  audit/               # Stage 8 — append-only audit log
  persistence/         # SQLAlchemy engine/session + repositories (SQLite)
  metrics/             # Stage 9 — metric computation
  evaluation/          # batch runner + baseline vs RevGuard report
  integrations/razorpay/  # SDK client + webhook signature verify (later)
  api/                 # FastAPI app: webhooks + dashboard/data endpoints (later)
```

Supporting:
```
data/synthetic/        # generated cases per scenario
scripts/               # generate_synthetic.py, run_eval.py (Typer CLI)
tests/unit, tests/integration
frontend/              # dashboard (decided in Phase 11; see §7)
docs/                  # these specs
```

## 4. AI provider abstraction

```
AIProvider (interface)
  ├── MockProvider / MockDiagnoser   # deterministic, offline, default
  └── AnthropicProvider              # Claude, added Phase 5, env-configured
```

- The application depends **only** on the `AIProvider` interface.
- Provider is selected via environment variable (e.g. `REVGUARD_AI_PROVIDER=mock|anthropic`).
- No assumption of Anthropic credits/keys. Default runtime path is fully offline.
- Details in AGENT_SPEC.md.

## 5. Data contracts (stage boundaries)

Each arrow in the pipeline is a validated Pydantic model, making each stage independently
testable:

- `Event` → raw input.
- `RevenueRiskSignal` → detector output.
- `RecoveryCase` → persistent unit of work; has `CaseStatus`.
- `ActionProposal` → AI output only: `ActionType` (enum whitelist) + params + rationale +
  confidence. Cannot express an unlisted action.
- `PolicyDecision` → `APPROVE | ESCALATE | STOP` + reason + matched rule(s).
- `ActionOutcome` → executor result.
- `RecoveryResult` → terminal case outcome for metrics.
- `EscalationRecord` → human queue entry (fields per PRODUCT_SPEC §6).

## 6. Persistence

- **SQLite + SQLAlchemy** initially (zero-infra; swap to Postgres later without changing
  repository interfaces).
- **AuditLog is append-only**: every stage transition records inputs/outputs/timestamps.
  It is the source of truth for metrics and evaluation reconstruction.
- Repositories mediate all DB access; domain/policy/AI code never touches the DB directly.

## 7. Frontend

- Deliverable is a **polished working dashboard**. Decision on exact stack is deferred to
  Phase 11, with a bias toward a **modern lightweight frontend that integrates cleanly
  with FastAPI**, choosing a separate frontend only if it does not add unnecessary
  complexity. Not built until backend/domain architecture is complete.

## 8. Technology choices (with reasons)

| Concern | Choice | Reason |
|---|---|---|
| Runtime | Python `>=3.12,<3.14` | Compatibility; ecosystem for Razorpay + AI |
| Contracts/validation | Pydantic v2 | Enforces "AI returns a typed proposal only"; rejects malformed/unlisted actions |
| Config | pydantic-settings + `.env` | Typed env; keeps test-mode keys out of code |
| Persistence | SQLite + SQLAlchemy | Zero-infra; append-only audit; Postgres-ready |
| AI | AIProvider → Anthropic (`anthropic` SDK), Mock default | Provider-agnostic; offline-first |
| Payments | `razorpay` SDK (Test Mode) behind adapter | Isolated; mock-first; no invented behavior |
| API/webhooks | FastAPI + httpx | Async webhook ingestion; OpenAPI for demo |
| CLI | Typer | Synthetic generation + batch eval |
| Tests | pytest | Deterministic per-stage + integration tests |

Initial dependency set is intentionally small (pydantic, pydantic-settings, sqlalchemy,
pytest, typer). `anthropic`, `razorpay`, `fastapi`/`httpx` are added only in the phases
that need them.

## 9. Runtime modes

- **Offline mode (default):** MockProvider + MockAdapter + simulated verification. Entire
  business logic runs with no external credentials or network.
- **Integrated mode (later):** AnthropicProvider and/or RazorpayTestAdapter enabled via
  env; webhook verification available but not required for core engine.
