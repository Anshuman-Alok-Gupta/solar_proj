# HANDOFF

Everything needed to continue development without the original agent.

## 1. Prerequisites

| Tool | Version used | Notes |
|---|---|---|
| Python | 3.12 (managed by `uv`) | `uv` 0.10+; venv at `backend/.venv` |
| Node.js | 22.x, npm 11 | |
| PostgreSQL | 18 (MSYS2 build) or 16 (Docker) | `psql`, `initdb`, `pg_ctl` on PATH |
| Docker (optional) | compose v2 | for `docker compose up` |

On this Windows machine all tool caches were moved to `D:\dev\...` via user env vars
(`UV_CACHE_DIR`, `UV_PYTHON_INSTALL_DIR`, `PIP_CACHE_DIR`, npm `cache`). Keep installs on D:.

## 2. Setup from scratch

```powershell
git clone <repo> solar_proj; cd solar_proj
copy .env.example .env            # then edit values (see section 3)

# Database (option A: local cluster)
initdb -D .local/pg -U postgres --auth=trust -E UTF8 --locale=C
pg_ctl -D .local/pg -l .local/pg.log start
psql -U postgres -h localhost -c "CREATE DATABASE solar"
# Database (option B): docker compose up db

# Backend
cd backend
uv sync                           # installs runtime + dev deps from uv.lock
uv run alembic upgrade head       # creates the 17 tables
uv run uvicorn app.main:app --reload --port 8000
#   -> http://localhost:8000/docs (OpenAPI), admin user auto-created from ADMIN_EMAIL/ADMIN_PASSWORD

# Frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:3000
```

First login: `ADMIN_EMAIL` / `ADMIN_PASSWORD`. Then Settings → create/update the plant
(capacity, lat/lon, timezone, tilt, azimuth, tariff). Data Management → "Test connection",
"Full history" (SEMS), "Sync weather". Model Lab → "Train all models". Forecasting →
"Issue forecast". Anomaly Centre → "Run detection".

## 3. Environment variables (`.env`)

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://user:pass@host:5432/solar` |
| `SECRET_KEY` | yes | JWT signing secret (rotate for production) |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | yes | bootstrap admin user |
| `CORS_ORIGINS` | yes | frontend origin(s), comma separated |
| `SEMS_ACCOUNT`, `SEMS_PASSWORD` | for live data | normal SEMS/SEMS+ app credentials (server-side only) |
| `SEMS_STATION_ID` | optional | only for multi-station accounts |
| `SEMS_SYNC_ENABLED`, `SEMS_SYNC_INTERVAL_SECONDS` | optional | scheduled polling (floor 300 s) |
| `WEATHER_SYNC_ENABLED`, `WEATHER_SYNC_INTERVAL_SECONDS` | optional | hourly Open-Meteo sync |
| `LLM_PROVIDER` | for AI | `openai_compatible` (default) \| `anthropic` \| `none` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | for AI | any OpenAI-compatible endpoint. Examples: Groq `https://api.groq.com/openai/v1` + `llama-3.3-70b-versatile`; OpenRouter `https://openrouter.ai/api/v1` + a `:free` model; Gemini `https://generativelanguage.googleapis.com/v1beta/openai` + `gemini-2.0-flash`; Ollama `http://localhost:11434/v1` + `llama3.1` (no key) |
| `LLM_FALLBACK_MODELS` | optional | comma-separated models tried when the primary returns 5xx/429/times out (e.g. `gemini-3-flash-preview,gemini-flash-lite-latest`); `LLM_TIMEOUT_SECONDS` (45) / `LLM_MAX_RETRIES` (1) |
| `LLM_TOOL_MODE` | optional | `auto` (native function calling, fallback to context injection), `native`, `context` |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | only if `LLM_PROVIDER=anthropic` | |
| `MODEL_DIR`, `UPLOAD_DIR` | optional | default `./data/models`, `./data/uploads` |
| `NEXT_PUBLIC_API_BASE_URL` | frontend | backend URL (build-time for Docker) |

Still needed from the owner: an LLM provider key/model (free tier is fine), the true panel
tilt, the electricity tariff (and export rate / installation cost for ROI).

## 4. Data formats

* **SEMS "Inverter Operation Report" (xlsx)** – one day, indicators as rows, 5-minute slots as columns; detected and unpivoted automatically (`app/ingestion/wide_reports.py`). Maps to `power_w` (kW→W) and `temperature_c`; interval energy = power × 5 min; aggregated to a daily row (`derived_from=interval_aggregate`, lower precedence than SEMS API daily values).
* **SEMS "Inverter Statistical Report" (xlsx)** – date columns, `Energy Generation(kWh)` row → daily rows.
* **Generic CSV/XLSX** – any table with a date/timestamp column plus generation / power / cumulative energy columns; roles are suggested and confirmed in the UI. Supported roles: `backend/app/ingestion/inspect.py::ROLES`.
* Timestamps without a zone are interpreted in the plant timezone (override per import with `mapping.timezone`); stored in UTC; daily rows use local civil dates.

## 5. Database

* Migrations: `cd backend && uv run alembic revision --autogenerate -m "msg" && uv run alembic upgrade head`.
* Tests use a separate `solar_test` database created automatically (`tests/conftest.py`); set `TEST_DATABASE_URL` to override.
* Reset synthetic/demo rows for a plant: `POST /api/plants/{id}/clear-synthetic`.

## 6. Machine learning

```powershell
# via API (recommended – records versions + metrics)
curl -X POST http://localhost:8000/api/plants/1/ml/train -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d "{}"
curl -X POST "http://localhost:8000/api/plants/1/forecasts/issue?days=3" -H "Authorization: Bearer <token>"
curl -X POST http://localhost:8000/api/plants/1/anomalies/detect -H "Authorization: Bearer <token>"
```

Artifacts: `data/models/plant_<id>/<timestamp>_<algorithm>.joblib` (gitignored). Code: `backend/app/ml/`.
Add an algorithm: extend `make_model()` and `ALGORITHMS` in `train.py`; SHAP works for any sklearn-compatible tree model.

## 7. Tests

```powershell
cd backend;  uv run ruff check . ; uv run pytest -q
cd frontend; npx tsc --noEmit ; npm run lint ; npm run build
```

Latest local run (2026-11-12): see the bottom of this file.

## 8. Deployment

### Live (free tiers) – deployed 2026-11-12

| Piece | Host | Where |
|---|---|---|
| Frontend (Next.js) | Vercel Hobby, project `solar-proj` | https://solar-proj-nine.vercel.app – `frontend/vercel.json` pins the Next.js preset |
| Backend (FastAPI, Docker) | Render free, Blueprint `render.yaml`, service `solar-backend-9mkn` (Singapore) | https://solar-backend-9mkn.onrender.com (`/api/health`, `/docs`) |
| Database | Neon free, project `solar-proj` (aws-ap-southeast-1, Postgres 18) | connection string only in Render env `DATABASE_URL` |
| Repo | GitHub private `Anshuman-Alok-Gupta/solar_proj` | Render auto-deploys on push to `main` (changes under `backend/` only) |

* **Secrets** live only in the Render service env (DATABASE_URL, ADMIN_*, SEMS_*, LLM_API_KEY, CORS_ORIGINS)
  and Vercel env (`NEXT_PUBLIC_API_BASE_URL`, build-time). Non-secret settings are in `render.yaml`.
* **Frontend redeploy:** `cd frontend && npx vercel@latest deploy --prod` (unless the Vercel GitHub app was
  given repo access, in which case pushes deploy automatically). Changing the backend URL requires a rebuild.
* **CORS:** backend `CORS_ORIGINS` must equal the Vercel origin exactly.
* **Ephemeral disk:** Render free has no persistent disk. Model artifacts are stored in Postgres
  (`model_artifacts`, migration 0002) and re-cached on first use; uploads under `/tmp` are temporary.
* **Sleep:** Render free sleeps after ~15 min idle (cold start ~1 min). A free cron-job.org job hitting
  `/api/health` every 10 min keeps it awake (one service fits in the free monthly instance hours);
  the in-process weather/SEMS scheduler only runs while awake.
* **Gemini free tier** occasionally returns 503 "high demand"; `LLM_FALLBACK_MODELS` lists models tried in
  order (sticky per answer so tool-call thought signatures stay valid).
* Data was migrated with `pg_dump -Fc --no-owner --no-privileges` → `pg_restore` into Neon.

### Docker / self-hosted

* `docker compose up --build` starts `db`, `backend` (runs migrations, then uvicorn) and `frontend` (standalone Next build). Set secrets in `.env` first; change `NEXT_PUBLIC_API_BASE_URL` build arg to the public backend URL.
* Without Docker: run uvicorn behind a reverse proxy and, for the frontend, `npm run build` then `node .next/standalone/server.js` (copy `.next/static` and `public` next to it as the Dockerfile does; `npm start` also works locally but warns because `output: "standalone"` is set). Schedule nothing extra – the API process runs the SEMS/weather jobs.
* Never commit `.env`, `data/uploads`, `data/samples/private`, model artifacts.

## 9. Next development priorities

See `PROJECT_STATUS.md` → "Incomplete / next priorities".

## 10. Latest test summary (2026-11-12, local Windows machine)

| Check | Result |
|---|---|
| `uv run ruff check .` (backend) | All checks passed |
| `uv run pytest -q` (backend, PostgreSQL 18, `solar_test` DB) | **34 passed** in ~20 s |
| `npx tsc --noEmit` (frontend) | clean |
| `npm run lint` (frontend) | 0 errors, 1 intentional warning (hard redirect on 401) |
| `npm run build` (frontend, Next 16 / Turbopack) | success, 9 routes |
| Live stack smoke test | backend `/api/health` 200; all 9 frontend routes 200; CORS preflight 200; every page's API calls 200 with real data (61 days, 901.2 kWh) |
| Real integrations | SEMS web + legacy login OK, full + incremental sync OK; Open-Meteo observed (61 d) + forecast (7 d) sync OK; import of both SEMS export types OK |
| Not executed here | `docker compose up` (Docker not installed on this machine), live LLM provider call (no key configured yet) |
