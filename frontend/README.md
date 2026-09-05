# RevGuard Dashboard (Phase 11)

A thin React + TypeScript (Vite) dashboard over the existing RevGuard FastAPI backend. It adds
**no** business logic — every number comes from the API (`/metrics`, `/cases`, `/cases/{id}`,
`/cases/{id}/audit`, `/evaluation`) and every action calls an existing endpoint (`POST /events`,
`POST /cases/{id}/run`). No secrets are read or stored in the frontend.

## What it shows

- **Overview** — revenue at risk, verified recovered amount, recovery rate, active & escalated
  cases, plus a per-workflow breakdown.
- **Cases** — the case list (workflow, risk, amounts, status, attempts); click a row for detail.
- **Case detail** — the full pipeline, with the **AI recommendation** (advisory) visually
  separated from the **PolicyEngine decision** (APPROVE / ESCALATE / STOP), execution result,
  verification result, recovered amount, and the complete audit timeline. Recovery is shown as
  confirmed **only** after verification.
- **Evaluation** — Baseline vs RevGuard on the seeded synthetic batch, with workflow breakdown.
- **Demo** — one-click synthetic scenarios and data refresh.

## Run

```bash
# 1. Start the backend (from the repo root), e.g.:
#    py -m uvicorn revguard.api.app:create_api_app --factory --reload
#    (set ANTHROPIC_API_KEY + Razorpay Test Mode creds to enable POST /cases/{id}/run)

# 2. Start the dashboard
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Configure the API base URL with `VITE_API_BASE_URL` (defaults to `http://localhost:8000`). The
backend allows the Vite dev origin via CORS (`REVGUARD_CORS_ALLOW_ORIGINS`).

## Verify

```bash
npm run build      # type-check + production build
npm test           # vitest unit/component tests
```
