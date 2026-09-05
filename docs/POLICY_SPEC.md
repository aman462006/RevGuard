# RevGuard — Policy Engine Specification

> Status: Draft for approval. The policy engine is deterministic and is the ONLY gate
> before execution. Parameter *values* below are placeholders to be finalized in Phase 4;
> the *rules themselves* are required.

## 1. Role

The policy engine takes `(RecoveryCase, ActionProposal, case history/state)` and returns a
deterministic `PolicyDecision`:

```
PolicyDecision = APPROVE | ESCALATE | STOP
  + reason
  + matched_rule_id(s)
  + (for APPROVE) the sanctioned action + parameters
```

Properties:

- **Deterministic & side-effect free** — pure functions over case state. Same inputs →
  same decision. No network, no DB writes inside decision logic.
- **Single gate** — no executor may run without an `APPROVE` decision object.
- **Executor enforcement** — the executor re-checks that it received an `APPROVE`
  referencing the exact action; otherwise it rejects.

## 2. Decision precedence

Evaluated in a fixed order; the first terminal condition wins:

1. **Hard STOP conditions** (already-recovered, do-not-contact for contact actions, case
   expired, duplicate event) → `STOP`.
2. **Escalation conditions** (amount over escalation threshold, max attempts reached with
   unresolved risk, AI recommended escalation, low-confidence + high-value) → `ESCALATE`.
3. **Guardrail checks** (cooldown active, idempotency conflict, amount over action cap,
   permission not allowed for workflow) → `STOP` or hold, per rule.
4. Otherwise → `APPROVE` the proposed (or policy-sanctioned) action.

Exact ordering finalized in Phase 4 and covered by a decision truth-table test suite.

## 3. Enforced controls (all required)

| Control | Rule (deterministic) |
|---|---|
| **Maximum retry attempts** | Per-case attempt counter capped; exceeding → ESCALATE or STOP. |
| **Action cooldowns** | Same action type cannot repeat before a cooldown window elapses. |
| **Idempotency** | Each intended action has an idempotency key; duplicates are not re-executed. |
| **Amount thresholds** | Per-action amount caps; values above cap cannot be auto-approved. |
| **Stopping rules** | Bounded workflow: attempt/time/step budget exhausted → STOP (terminal). |
| **Customer opt-out / do-not-contact** | Contact actions (reminder, payment link) blocked when customer is opted out. |
| **Escalation thresholds** | Amount-at-risk (or risk score) above threshold → ESCALATE to human queue. |
| **Case expiration** | Cases past their max lifetime are closed; no further actions. |
| **Duplicate event protection** | Repeated inbound events for the same underlying risk do not spawn duplicate work. |
| **Already-recovered protection** | If verification shows recovery, no further recovery actions are permitted. |

## 4. Per-workflow permissions (whitelist)

The engine holds an explicit **action whitelist per workflow**. An action not whitelisted
for a workflow can never be approved, regardless of AI proposal.

| Workflow | Typical permitted actions (finalized in Phase 4) |
|---|---|
| A — Payment degradation | Analytics/root-cause classification; recommend intervention; escalate. (Recovery actions constrained; analytics-focused.) |
| B — Failed subscription / mandate | Retry payment, create payment link, send reminder, escalate, stop. |
| C — Checkout abandonment | Recovery contact/payment link (if appropriate), stop. |
| D — B2B overdue receivables | Reminder, record promise-to-pay, escalate, stop. |

## 5. Bounded recovery sequences

Each workflow defines an ordered, **bounded** sequence of steps with a hard cap. The
orchestrator advances the sequence only via APPROVE decisions; ESCALATE and STOP are
terminal branches. No unbounded loops.

## 6. Escalation output

When the decision is ESCALATE, the engine produces the data needed to create an
`EscalationRecord`: case ID, reason, amount at risk, customer, AI recommendation, the
policy decision itself, timestamp, status. Humans resolve records via the escalation
queue.

## 7. Baseline vs RevGuard interaction

- **Baseline** strategy applies fixed rules **without** AI diagnosis (e.g. fixed retry
  then stop). It uses a simplified/deterministic decision path.
- **RevGuard** uses AI diagnosis feeding the full policy engine above.
- Both run through the same executor/verification so results are comparable on one batch.

## 8. Testing posture

- Truth-table unit tests for each control and for decision precedence.
- Boundary tests for every threshold/cooldown/expiration.
- Property: **no path produces an executed action without a matching APPROVE.**
