# PROJECT_STATUS

Last updated: 2026-11-12 (see git log for later changes)

## Completed

- [x] Greenfield monorepo: `backend/` (FastAPI, Python 3.12 via uv), `frontend/` (Next.js 16, TS, Tailwind 4, shadcn/ui, Recharts), local PostgreSQL 18 cluster, Alembic migration `0001`.
- [x] Verified the real installation: GoodWe GW6000-SDT-20, SEMS+ account, one station, daily history since 2026-09-12 (details in `docs/experiments.md`; identifiers only in `.env` / DB).
- [x] SEMS integration (`backend/app/integrations/sems`): web + legacy login, station/device discovery, daily statistics (full + incremental), live telemetry snapshot, rate-limit cool-down, sync logs, status endpoint, scheduled polling (opt-in).
- [x] Weather integration (Open-Meteo): ERA5 history, recent-day bridge, 7-day forecast with issue history, hourly + daily tables, scheduled hourly sync.
- [x] CSV/XLSX/XLS ingestion with header detection, SEMS wide-report unpivot, column-role suggestions, unit normalisation, timezone handling, quality report, preview, idempotent commit, import history.
- [x] Unified storage with source precedence (API > import > synthetic) and provenance on every row.
- [x] Analytics API: summary, daily/weekly/monthly, distribution, weather correlation, period comparison, intraday, savings (range when unmetered), monthly savings.
- [x] ML: feature pipeline with leakage test, 3 baselines + RF/XGBoost/LightGBM/CatBoost, expanding-window CV + chronological test, model registry with artifacts, selection policy, SHAP (tree models) + rule explanations (baselines), forecasts with empirical intervals, backtest endpoint, live-forecast scoring.
- [x] Anomaly detection: residual, coverage, intraday swings, data-quality, Isolation Forest; evidence + recommended checks; status workflow.
- [x] Reports: daily/weekly/monthly preview, generate, list, CSV export.
- [x] AI Energy Analyst: provider-agnostic (OpenAI-compatible default, Anthropic optional), 12 backend tools, native tool-calling loop + context-injection fallback, conversation history with tool trace.
- [x] Frontend pages: Overview, Energy Analytics, Forecasting, Anomaly Centre, AI Energy Analyst, Model Lab, Data Management (SEMS/weather status, import wizard, logs), Settings, Login. Loading/empty/error states on every data block.
- [x] Tests: backend pytest suite (energy store precedence, ingestion, SEMS client with mocked HTTP, ML leakage/splits/end-to-end, API incl. access control, imports, reports, AI loop with fake providers).
- [x] Dockerfiles, docker-compose, GitHub Actions CI, `.env.example`, docs.

- [x] Live LLM: Google Gemini via its OpenAI-compatible endpoint (`gemini-3.5-flash` + fallbacks); native tool calling verified end to end on the deployed stack (thought signatures echoed back).
- [x] Deployed (2026-11-12): Vercel frontend, Render backend (Blueprint), Neon Postgres with the real data; model artifacts persisted in Postgres. See `HANDOFF.md` §8.
- [x] Keep-awake: cron-job.org pings `/api/health` every 10 min (first external ping verified in Render logs 2026-11-12 11:40 IST, 200 OK).
- [x] Scheduled SEMS polling enabled on Render (`SEMS_SYNC_ENABLED=true`, every 900 s; first run ~90 s after each start) to build live readings and score live forecasts.
- [x] Reports page (`/reports`): live preview, save, PDF export (fpdf2, `GET /reports/{id}/pdf`) and CSV export.
- [x] Sharing: one-time invite links (viewer / editor roles, 7-day expiry, token stored as SHA-256), member management in Settings, `/invite/<token>` sign-up page; viewers are read-only server-side (AI analyst still allowed). Change-password in Settings. Migration `0003`.
- [x] Net-metering wording for savings (MSEB/MSEDCL: generation offsets purchases at the bill's per-unit rate; year-end surplus paid at the settlement rate).
- [x] Frontend automated tests (Vitest + Testing Library: API client, formatters, components) in CI.
- [x] Measured training memory: train all 7 models + SHAP + forecast + detect peaks at ~360 MB RSS, inside Render free's 512 MB (`MALLOC_ARENA_MAX=2` set to limit fragmentation).

## Incomplete / next priorities

1. Set the per-unit energy charge from the MSEB bill as the import tariff (and the year-end surplus settlement rate as the export rate) in Settings to turn savings into numbers; the plant has net metering and no tariff income.
2. Confirm the real panel tilt (currently assumed 18°).
3. Import more historical 5-minute operation reports (one per day from the SEMS app) to enable intraday anomaly checks across days.
4. After a few days of SEMS polling: verify live readings accumulate and live forecast scoring works on the deployed stack.
5. Optional: e-mail delivery of invite links (currently copy/paste; no SMTP configured).

## Known issues / caveats

- Only ~60 days of history: model metrics are noisy; the selected model is a weather-scaled baseline (documented in `docs/experiments.md`).
- SEMS legacy endpoints (`GetMonitorDetailByPowerstationId`, power chart) return empty for this account; not used.
- "new" SEMS login mode fails with code 100004 for this account; the client falls back to web/legacy automatically.
- Open-Meteo archive lags ~6 days; recent days use archived forecasts (labelled by `source`).
- Docker Compose has never been run on the dev machine (Docker not installed) and is out of scope; the backend image is built on Render and CI runs tests on PostgreSQL.
- Neon free: 0.5 GB storage / limited compute hours. Render free: 512 MB RAM (training measured at ~360 MB peak with the current ~60 days; re-check as history grows; fallback is training locally with `DATABASE_URL` pointed at Neon).
- The ESLint rule `@next/next/no-location-assign-relative-destination` warns on the 401 redirect in `frontend/src/lib/api.ts` (intentional hard redirect to clear state).

## How to run (local, no Docker)

```powershell
# PostgreSQL (cluster lives in .local/pg, created with initdb)
pg_ctl -D .local/pg -l .local/pg.log start

# Backend
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
npm install
npm run dev   # http://localhost:3000  (login: ADMIN_EMAIL / ADMIN_PASSWORD from .env)
```

## Test status (2026-11-12)

- Backend: `ruff check` clean, `pytest` **48 passed** (energy store, ingestion, SEMS client mocked, ML, API, AI loop, provider compat, sharing/roles, PDF).
- Frontend: `tsc` clean, `eslint` 0 errors, `vitest` **15 passed**, `next build` success (11 routes).
- Live smoke test against the running stack: all pages and their API calls return 200 with the real 61-day dataset.
- Details and commands: `HANDOFF.md` §7 and §10.

```
cd backend && uv run pytest -q
cd frontend && npx tsc --noEmit && npm run lint && npm test && npm run build
```
