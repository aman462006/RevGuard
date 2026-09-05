# RevGuard — Evaluation & Metrics Specification

> Status: Draft for approval. No claim of AI superiority is made unless the evaluator
> actually computes the comparison on the same batch.

## 1. Purpose

Process a batch of synthetic cases through the recovery engine and compute metrics that
quantify money recovered and workflow behavior, and compare **Baseline** vs **RevGuard**
on the **same input batch**.

## 2. Strategies compared

| Strategy | Definition |
|---|---|
| **Baseline** | Deterministic, **rule-only** recovery. Fixed behavior, no AI diagnosis. |
| **RevGuard** | Detection + contextual **AI diagnosis** + policy engine + bounded workflow. |

Both consume the identical batch and the identical executor/verification substrate so the
only variable is the diagnosis/decision strategy.

## 3. Required metrics

Computed per batch (and per strategy):

- total cases
- revenue at risk
- revenue recovered
- recovery rate (recovered / at risk)
- escalated cases
- stopped cases
- unresolved cases
- recovery by workflow type (A, B, C, D)
- baseline vs RevGuard comparison (side by side on same batch)

## 4. Synthetic data

- Generator (CLI, Typer) produces labeled cases per workflow A–D with fields sufficient
  for detection, diagnosis context, policy evaluation, and a **ground-truth recoverability**
  so verification can simulate outcomes deterministically in offline mode.
- Stored under `data/synthetic/`. Seeded for reproducibility.
- No real customer data; no PII.

## 5. Offline verification model

- In offline mode, whether an action "recovers" money is decided by the synthetic case's
  ground-truth + the action taken (deterministic/seeded), **not** by a live payment
  provider. This keeps evaluation reproducible and independent of any public webhook URL.
- In integrated mode, verification can use webhook/polling (see ARCHITECTURE §7 /
  verification module).

## 6. Batch runner

- Input: a batch (generated or file-based) + a strategy selector.
- For each case: run the full pipeline, record every step to the audit log, and produce a
  `RecoveryResult`.
- Output: a metrics report object + a human-readable report (CLI and, later, dashboard).

## 7. Comparison methodology

- Run Baseline over batch → metric set M_baseline.
- Run RevGuard over the same batch → metric set M_revguard.
- Report both plus deltas (e.g. recovery rate difference, revenue recovered difference,
  escalation/stop counts). Present the comparison as-measured; no unearned claims.

## 8. Testing posture

- Deterministic fixtures with known ground truth → assert exact metric values.
- Reproducibility test: same seed → identical metrics.
- Comparison test: evaluator emits both strategy result sets for one batch.
