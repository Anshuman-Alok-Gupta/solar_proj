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

## Incomplete / next priorities

1. Connect a free LLM provider: fill `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` in `.env` and try the analyst (both tool modes are tested with fakes, not yet with a live provider).
2. Enable scheduled SEMS polling (`SEMS_SYNC_ENABLED=true`) to build 15-minute live readings; verify the first days of live forecast scoring.
3. Confirm the real panel tilt (currently assumed 18°) and set the electricity tariff in Settings to turn savings into numbers.
4. Import more historical 5-minute operation reports (one per day from the SEMS app) to enable intraday anomaly checks across days.
5. PDF export of reports (CSV exists); multi-user invitations (schema supports ownership, no UI).
6. Docker Compose has not been executed on the dev machine (Docker not installed); CI runs tests on PostgreSQL in GitHub Actions.
7. Frontend automated tests (only type-check + lint + build are automated).

## Known issues / caveats

- Only ~60 days of history: model metrics are noisy; the selected model is a weather-scaled baseline (documented in `docs/experiments.md`).
- SEMS legacy endpoints (`GetMonitorDetailByPowerstationId`, power chart) return empty for this account; not used.
- "new" SEMS login mode fails with code 100004 for this account; the client falls back to web/legacy automatically.
- Open-Meteo archive lags ~6 days; recent days use archived forecasts (labelled by `source`).
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

- Backend: `ruff check` clean, `pytest` **34 passed** (energy store, ingestion, SEMS client mocked, ML, API, AI loop).
- Frontend: `tsc` clean, `eslint` 0 errors, `next build` success (9 routes).
- Live smoke test against the running stack: all pages and their API calls return 200 with the real 61-day dataset.
- Details and commands: `HANDOFF.md` §7 and §10.

```
cd backend && uv run pytest -q
cd frontend && npx tsc --noEmit && npm run lint && npm run build
```
