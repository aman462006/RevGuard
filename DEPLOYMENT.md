# Deploying RevGuard

Two services: the **FastAPI backend** on **Render** and the **React/Vite frontend** on **Vercel**.
Deploy the backend first (you need its URL to configure the frontend).

> Tip: the fastest, most reliable judge demo is the backend in **demo mode** (no credentials, no
> external calls, no webhook). Switch to production mode later for the live Razorpay path.

---

## 1. Backend → Render (FastAPI)

Render can't run on Supabase — Supabase is a database, not a Python app host. Use Render (or
Railway / Fly.io). This repo ships a `render.yaml` blueprint.

1. Push this repo to GitHub (already done: `aman462006/RevGuard`).
2. Render Dashboard → **New → Blueprint** → connect the `RevGuard` repo → **Apply**.
   - It reads `render.yaml`: Python service, build `pip install ".[api,gemini,razorpay]"`,
     start `uvicorn revguard.api.app:create_api_app --factory --host 0.0.0.0 --port $PORT`,
     health check `/health`.
3. It deploys in **demo mode** by default (no keys needed). Wait for "Live", then note the URL,
   e.g. `https://revguard-api.onrender.com`.
4. Verify: open `https://<backend>/health` → should return `{"status":"ok"}`, and
   `https://<backend>/status` → shows the mode.

> Free instances **cold-start** (~30–60 s) after inactivity. Hit `/health` once to warm it up
> before demoing.

## 2. Frontend → Vercel (React/Vite)

1. Vercel Dashboard → **Add New → Project** → import the `RevGuard` repo.
2. **Root Directory:** `frontend` (important — the app lives in the subfolder).
   Framework auto-detects as **Vite**; `frontend/vercel.json` sets the build + SPA rewrite.
3. **Environment Variable:**
   `VITE_API_BASE_URL = https://<your-render-backend-url>`  (no trailing slash)
4. **Deploy.** Note the URL, e.g. `https://revguard.vercel.app`.

## 3. Wire them together (CORS)

The browser call from Vercel → Render is blocked until the backend allows that origin.

1. Render → your service → **Environment** → set
   `REVGUARD_CORS_ALLOW_ORIGINS = ["https://<your-vercel-app>.vercel.app"]`  (JSON list)
2. Save → Render redeploys. Reload the Vercel site — cases/metrics should now load.

---

## Optional: production mode (real Gemini + Razorpay Test Mode)

On Render → Environment:

- `REVGUARD_MODE = production`
- `REVGUARD_AI_PROVIDER = gemini` and `GEMINI_API_KEY = <your key>`
- `RAZORPAY_KEY_ID = rzp_test_...`, `RAZORPAY_KEY_SECRET = ...`, `RAZORPAY_WEBHOOK_SECRET = ...`

Then in the **Razorpay Dashboard → Webhooks**, add:
`https://<your-backend>/webhooks/razorpay` with the same `RAZORPAY_WEBHOOK_SECRET`, subscribed to
payment / payment-link **paid** events. A public backend URL makes the `*.paid` webhook actually
deliverable, so a paid Test Mode link reconciles to RECOVERED automatically (or use the
**Check payment status** button).

Never commit these keys — set them only in the Render dashboard. `.env` is git-ignored.

---

## Fill in the links

After both are live, update the top of [`README.md`](README.md):

```
**Live demo:** https://<your-vercel-app>.vercel.app  ·  **API:** https://<your-backend>.onrender.com
```

then `git add README.md && git commit -m "docs: add deployment links" && git push`.
