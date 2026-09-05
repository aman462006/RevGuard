# RevGuard — AI Agent Specification

> Status: Draft for approval. The AI is a reasoning/recommendation component only.

## 1. Hard boundary

The AI **never** executes external side effects. It:

- receives a read-only view of a `RecoveryCase` (+ derived context),
- returns exactly one typed `ActionProposal`,
- has **no** access to executors, repositories/DB, network actions, or the Razorpay SDK.

Enforcement is structural: the diagnosis module imports only domain models and the
`AIProvider` interface. Anything the AI "wants" to do is expressed as a proposal that the
deterministic policy engine may reject.

## 2. Provider abstraction

```
AIProvider (interface)
    diagnose(case_context: CaseContext) -> ActionProposal

  ├── MockProvider (a.k.a. MockDiagnoser)   # deterministic, offline, DEFAULT
  └── AnthropicProvider                     # Claude; added in Phase 5
```

- Application code depends **only** on `AIProvider`.
- Selected via env (e.g. `REVGUARD_AI_PROVIDER`), default `mock`.
- **No assumption of Anthropic key/credits.** Offline path must always work.
- Adding/removing a provider must not change any consumer code.

### 2.1 Configuration (environment variables)

| Variable | Purpose | Default |
|---|---|---|
| `REVGUARD_AI_PROVIDER` | `mock` \| `anthropic` | `mock` |
| `ANTHROPIC_API_KEY` | Claude credential (only if provider=anthropic) | unset |
| `REVGUARD_AI_MODEL` | model id for Anthropic provider | (set in Phase 5) |
| `REVGUARD_AI_TIMEOUT_S` | provider call timeout | small default |

Exact model id/params are finalized in Phase 5; not invented here.

## 3. MockDiagnoser (Phase 5, first)

- Deterministic mapping from case features → `ActionProposal` (no randomness, or seeded).
- Covers all four workflows so the full pipeline + evaluator run offline.
- Serves as the reference oracle for tests: business rules are validated against Mock,
  not against live LLM behavior.

## 4. AnthropicProvider (Phase 5, second)

- Wraps the `anthropic` SDK behind `AIProvider`.
- **Schema-constrained output:** the model must return data conforming to
  `ActionProposal`; any non-conforming output is rejected and treated as a failed
  diagnosis (never executed).
- Timeouts/failures degrade safely: a failed AI call yields no proposal → policy engine
  handles as ESCALATE/STOP per POLICY_SPEC, never as an unchecked action.

## 5. ActionProposal schema (AI output contract)

Fields (final types defined in `domain/proposals.py`):

- `action_type: ActionType` — enum **whitelist** (e.g. RETRY_PAYMENT,
  CREATE_PAYMENT_LINK, SEND_REMINDER, RECORD_PROMISE_TO_PAY, RECOMMEND_ESCALATION,
  RECOMMEND_STOP, NO_ACTION). The exact enum is fixed in Phase 1 and mirrors the actions
  the executor + policy engine understand.
- `parameters: dict` — validated per action type.
- `rationale: str` — human-readable reason (for audit/dashboard).
- `confidence: float` — 0..1.

The AI **cannot** propose an action outside `ActionType`. Proposing escalation/stop is a
recommendation only; the deterministic policy engine makes the binding decision.

## 6. Prompt structure (Anthropic provider)

- System role: constrains the model to the RevGuard task, the allowed actions, and the
  required output schema.
- Input: sanitized, read-only case context (no secrets, no PII beyond what a customer
  reference requires).
- Output: structured object validated against `ActionProposal`.
- Prompts live in `diagnosis/prompts.py`. No tool-use / no function execution is granted
  to the model.

## 7. Testing posture

- Deterministic unit tests use `MockProvider`.
- AnthropicProvider tests validate **schema conformance and safe failure handling**, not
  the "intelligence" of answers.
- No business rule depends on live LLM output for correctness.
