# AI Solar Intelligence Platform

Turns a household rooftop PV installation's raw data into analytics, next-day forecasts,
weather-adjusted anomaly alerts, reports and a grounded AI energy analyst.

Built for a real 5 kW system with a GoodWe GW6000-SDT-20 inverter monitored through
GoodWe SEMS+, architected for multiple plants.

> Status: working end-to-end MVP (backend, PostgreSQL, SEMS + weather + file ingestion, trained
> and evaluated forecasting models, anomaly detection, reports, provider-agnostic AI analyst,
> 8-page dashboard, tests, CI, Docker). See `PROJECT_STATUS.md` and `HANDOFF.md`.

## Features

| Area | What it does |
|---|---|
| **Data sources** | GoodWe SEMS+ API (daily history + live telemetry, polite polling), CSV/XLSX/XLS imports with column mapping and quality report (SEMS exports auto-detected), Open-Meteo weather (observed + forecast, tilted-plane irradiance) |
| **Provenance** | Every row is labelled `sems_api` / `csv_import` / `synthetic`; weather is `observed` vs `forecast`; partial days are flagged; synthetic data is never shown as real |
| **Analytics** | Daily/weekly/monthly series, distribution, weather vs generation, period comparison, 5-minute power curves, savings range when unmetered |
| **Forecasting** | Next-day kWh from forecast weather + recent production; empirical 10–90 % interval; live forecasts scored against actuals; backtest view |
| **Model Lab** | Baselines vs Random Forest / XGBoost / LightGBM / CatBoost, expanding-window CV + chronological hold-out, feature importance, SHAP, training history, manual activation |
| **Anomaly Centre** | Unexpected drops, zero days, missing data, intraday swings, underperformance streaks, data-quality flags, Isolation Forest; each with evidence and recommended checks |
| **AI Energy Analyst** | Chat grounded by 12 backend tools; works with any OpenAI-compatible LLM (free tiers included) or Anthropic; tool trace shown per answer |
| **Reports** | Daily / weekly / monthly performance reports with weather context, anomalies, forecast accuracy and savings; CSV export |
| **Security** | JWT auth, plant-level access control, secrets server-side only, input validation, masked identifiers |

## Screenshots

_Placeholders – add images under `docs/screenshots/`._

| Overview | Energy Analytics | Model Lab |
|---|---|---|
| ![overview](docs/screenshots/overview.png) | ![analytics](docs/screenshots/analytics.png) | ![model-lab](docs/screenshots/model-lab.png) |

## Architecture

```
Next.js 16 (TS, Tailwind, shadcn/ui, Recharts)  ──JWT──▶  FastAPI + SQLAlchemy + Alembic  ──▶  PostgreSQL
                                                             │  services · ingestion · ml · ai
                                                             ├─ GoodWe SEMS+ (unofficial web API)
                                                             ├─ Open-Meteo (forecast / ERA5 / hist. forecast)
                                                             └─ any OpenAI-compatible LLM (or Anthropic)
```

Full description and decision log: `docs/architecture.md`. ML methodology and results: `docs/experiments.md`.

## Quick start (local)

```powershell
copy .env.example .env   # edit DATABASE_URL, ADMIN_*, SEMS_*, LLM_*
# PostgreSQL: docker compose up db   (or a local cluster, see HANDOFF.md)
cd backend  && uv sync && uv run alembic upgrade head && uv run uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

Open http://localhost:3000, sign in with `ADMIN_EMAIL` / `ADMIN_PASSWORD`, configure the plant in
Settings, then use Data Management to sync SEMS / weather or import exports.

### Docker

```bash
docker compose up --build    # db + backend (auto-migrate) + frontend
```

## Repository layout

```
backend/   FastAPI app (app/api, app/services, app/ingestion, app/integrations, app/ml, app/ai), alembic, tests
frontend/  Next.js app (src/app pages, src/components, src/lib)
docs/      architecture.md, experiments.md
data/      models/ (artifacts, gitignored), uploads/ (gitignored), samples/
```

## Tests

```powershell
cd backend  && uv run ruff check . && uv run pytest -q
cd frontend && npx tsc --noEmit && npm run lint && npm run build
```

## Honesty notes

* Backtest metrics use observed weather and therefore overstate real forecasting skill; the
  app tracks live forecast error separately.
* With ~60 days of history the simple weather-scaled baseline currently beats the boosted
  models; the Model Lab shows this openly and re-evaluates on demand.
* Without a smart meter, consumption/export are not measured; savings are shown as a range.
* The SEMS web API is undocumented by GoodWe and may change; the client fails safely and
  CSV imports remain a full fallback.

## License

Private project. No license granted.
