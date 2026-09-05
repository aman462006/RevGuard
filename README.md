# RevGuard — AI-Powered, Policy-Controlled Revenue Recovery Agent

RevGuard detects revenue at risk, uses AI to *recommend* one recovery action, runs that
recommendation through a **deterministic policy engine** (the single gate before anything
executes), executes approved actions against **Razorpay Test Mode**, and marks money recovered
**only** from independently verified payment status. Every step is written to an append-only
audit trail.

> Built for **Razorpay Track 03 — AI Revenue Recovery**.

**Live demo:** `<ADD_DEPLOYED_FRONTEND_URL>`  ·  **API:** `<ADD_DEPLOYED_BACKEND_URL>`

---

## 1. Overview

Businesses lose revenue continuously and quietly: subscription charges fail, checkouts are
abandoned, invoices go overdue, and a payment method silently starts declining. RevGuard is an
autonomous agent that turns those signals into **bounded, auditable, verified recovery actions**.

Rather than just flagging problems, it drives the full workflow — detect the risk, ask an AI to
diagnose and recommend an action, let a deterministic policy engine authorize or refuse it,
execute only what is authorized through Razorpay Test Mode, and count recovery **only** once the
payment is verified. It covers four revenue-risk workflows:

- **Failed subscription** — a recurring charge was declined.
- **Checkout abandonment** — a customer left with items in the cart.
- **Overdue receivable** — an invoice is past due.
- **Payment degradation** — a spike in failures for a payment method.

The guiding principle is **honesty**: the AI advises, policy authorizes, the executor acts, and
the verifier confirms — and the UI never presents a simulation, a created link, or a benchmark as
if it were real recovered money.

## 2. The Problem

Most "recovery" tooling either (a) only reports problems and leaves a human to act, or (b) lets an
AI take actions directly — which is unsafe and unauditable. RevGuard separates the two concerns:

- **AI is advisory only.** It recommends; it can never execute or bypass the policy gate.
- **Recovery is verification-based.** `amount_recovered` comes only from a verified payment
  (Razorpay `paid` status / signature-verified webhook), never from AI output or a successful API
  call, and is always capped at the amount at risk.
- **Fail closed.** A malformed/unavailable AI response degrades to an escalation; a misconfigured
  production path returns HTTP `503` and never silently falls back to a mock.
- **Bounded and idempotent.** Retries follow a fixed schedule with a hard attempt cap; duplicate
  events, webhooks, and executions never double-act.

## 3. How It Works — The Pipeline

```
                 ┌─────────┐   advisory    ┌────────────┐  authorize   ┌──────────┐   verified
   events ─────► │ DETECT  │ ────────────► │  DIAGNOSE  │ ───────────► │  POLICY  │ ───────────►
                 │ (rules) │               │   (AI)     │              │ (engine) │
                 └─────────┘               └────────────┘              └────┬─────┘
                                                                            │ APPROVE
                                                                            ▼
                                    RECOVERED ◄──── VERIFY ◄──── EXECUTE (Razorpay Test Mode)
                                        │              │
                                        └──► ESCALATE / STOP  (₹0 recovered until verified)
```

```
DETECT → DIAGNOSE (AI) → POLICY (deterministic gate) → EXECUTE (Razorpay) → VERIFY → RECOVER / ESCALATE / STOP
```

1. **Detect** — deterministic detectors turn raw events into a `RevenueRiskSignal` (which
   workflow, how much is at risk, risk level).
2. **Diagnose** — the configured AI model (Gemini by default) recommends **one** action
   (retry payment, payment link, reminder, promise-to-pay, escalate, stop). Advisory only.
3. **Policy** — a pure, deterministic `PolicyEngine` returns exactly one decision:
   **APPROVE / ESCALATE / STOP**, based on a per-workflow action whitelist, amount thresholds,
   confidence, do-not-contact, bounded retries, and stopping rules. The AI cannot override it.
4. **Execute** — only an APPROVED action runs, through Razorpay Test Mode (a real order or
   payment link). A successful API call is **not** recovery.
5. **Verify** — recovery is confirmed only from the verified Razorpay payment status (polling or a
   signature-verified `*.paid` webhook). Otherwise the case waits, retries on a fixed schedule, and
   eventually **escalates to a human or stops** with ₹0 recovered.

Every transition is written to an append-only audit trail, so the entire decision path is
reconstructable.

## 4. Architecture

Modular monolith under `src/revguard/` — SQLAlchemy is confined to the persistence/audit layer;
domain models are pure Pydantic.

| Layer | Package | Responsibility |
|-------|---------|----------------|
| Detection | `detection/` | Turn events into `RevenueRiskSignal`s (4 workflows) |
| Diagnosis | `diagnosis/` | AI recommendation only — **Gemini** in production, deterministic `MockDiagnoser` offline |
| Policy | `policy/` | Deterministic `PolicyEngine` — the single APPROVE / ESCALATE / STOP gate |
| Execution | `execution/`, `integrations/razorpay/` | Route approved actions to Razorpay Test Mode |
| Verification | `verification/`, `integrations/razorpay/verifier.py` | Confirm recovery from verified payment status only |
| Orchestration | `orchestrator/` | The bounded control loop + case state machine |
| Persistence / Audit | `persistence/`, `audit/` | SQLite state + append-only audit log |
| API | `api/` | Thin FastAPI layer over the services |
| Dashboard | `frontend/` | React + TypeScript operations console |

See [`docs/`](docs/) for the full specifications.

## 5. Tech Stack

| Area | Technology |
|------|------------|
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2, SQLite |
| AI (diagnosis) | Google **Gemini** (default); Groq (Llama 3.3) and Anthropic Claude also supported |
| Payments | **Razorpay Test Mode** SDK (orders, payment links, signature-verified webhooks) |
| Frontend | React 18, TypeScript, Vite 5 (no UI framework — a hand-built editorial design system) |
| Testing / QA | pytest (backend), Vitest + Testing Library (frontend), Ruff (lint) |

## 6. Deployment

> Fill in the links below after deploying.

| Component | Platform | URL |
|-----------|----------|-----|
| Frontend (dashboard) | e.g. Vercel / Netlify | `<ADD_DEPLOYED_FRONTEND_URL>` |
| Backend (API) | e.g. Render / Railway | `<ADD_DEPLOYED_BACKEND_URL>` |

Deployment notes:

- The frontend reads the API base URL from `VITE_API_BASE_URL` (defaults to
  `http://localhost:8000`). Set it to the deployed backend URL at build time.
- Add the deployed frontend origin to the backend's `REVGUARD_CORS_ALLOW_ORIGINS`.
- Choose the mode per deployment via `REVGUARD_MODE` (`demo` or `production`) — it cannot be
  switched from the UI, which keeps the mode honest. For a reviewer walkthrough, a **demo**
  deployment needs no credentials; a **production** deployment needs the AI + Razorpay Test Mode
  keys below.

## 7. Project Structure

```
RevGuard/
├── src/revguard/          # Backend (detection, diagnosis, policy, execution, verification,
│   ├── detection/         #   orchestration, persistence, audit, api, integrations/razorpay)
│   ├── diagnosis/
│   ├── policy/
│   ├── execution/
│   ├── verification/
│   ├── orchestrator/
│   ├── integrations/razorpay/
│   ├── persistence/  audit/  metrics/  evaluation/  synthetic/
│   └── api/
├── frontend/              # React + TypeScript dashboard (Vite)
├── tests/                 # pytest suite (unit + integration)
├── docs/                  # specifications
├── pyproject.toml
└── .env.example           # copy to .env and fill credentials (never commit .env)
```

## 8. Getting Started

**Requirements:** Python 3.12.x recommended. The app runs fully offline on defaults.

### Backend

```powershell
# from repo root
py -m pip install -e ".[dev,api,gemini,razorpay]"
$env:PYTHONPATH = "src"
py -m uvicorn revguard.api.app:create_api_app --factory --reload --port 8000
```

### Frontend (second terminal)

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173
```

Open the dashboard, go to the **Demo** tab, and run a scenario to watch the full
detect → diagnose → policy → execute → verify pipeline. Use **Reset all cases** on the Cases tab
to start from scratch.

## 9. Environment Variables

Copy `.env.example` to `.env` and fill in what you need. `.env` is git-ignored — never commit real
keys. Offline/demo mode needs none of these.

| Variable | Purpose |
|----------|---------|
| `REVGUARD_MODE` | `demo` (offline test doubles) or `production` (live AI + Razorpay). Surfaced at `GET /status`. |
| `REVGUARD_AI_PROVIDER` | `gemini` (default), `groq`, or `anthropic`. |
| `GEMINI_API_KEY` / `GROQ_API_KEY` / `ANTHROPIC_API_KEY` | API key for the selected provider. |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | Razorpay **Test Mode** keys (`rzp_test_...`; live keys are rejected). |
| `RAZORPAY_WEBHOOK_SECRET` | Secret for signature-verifying `*.paid` webhooks. |
| `REVGUARD_DATABASE_URL` | SQLite URL (default `sqlite:///./revguard.db`). |
| `VITE_API_BASE_URL` (frontend) | Backend URL the dashboard calls (default `http://localhost:8000`). |

## 10. Demo vs Production Mode

Set `REVGUARD_MODE` at startup (shown honestly at `GET /status` and in the dashboard's status
strip):

- **`production`** — the live path: the configured AI provider (Gemini) diagnoses, approved
  actions hit **Razorpay Test Mode**, and recovery is confirmed only from verified payment status.
  Requires the AI + Razorpay Test Mode keys; otherwise `POST /cases/{id}/run` returns `503` (never
  a silent mock fallback). Reviewers can pay a Test Mode payment link (a test card, no real money)
  and click **Check payment status** to see a genuine verified recovery.
- **`demo`** — a credential-free rehearsal using deterministic offline test doubles. Detection and
  the PolicyEngine are the real ones; diagnosis, execution, and verification are simulated. Every
  WAITING case offers **Simulate payment received** and **Leave unpaid & verify** so any case can
  be driven to a final outcome without any real payment.

## 11. Tests, Lint & Build

```powershell
# Backend
$env:PYTHONPATH = "src"; py -m pytest      # 540+ tests
py -m ruff check .

# Frontend
cd frontend
npm test                                   # Vitest
npm run build                              # tsc --noEmit + production build
```

## Acknowledgements

Built with FastAPI, SQLAlchemy, Pydantic, React, Vite, and the Razorpay Test Mode SDK. AI
diagnosis is powered by Google Gemini (with Groq and Anthropic as alternatives).
