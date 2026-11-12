# Architecture

## Overview

```
┌──────────────────────────────┐       ┌────────────────────────────────────────────────────────┐
│  Next.js 16 frontend (3000)  │  JWT  │  FastAPI backend (8000)                                │
│  App Router, TS, Tailwind 4, │──────▶│  /api/auth  /api/plants/{id}/...                       │
│  shadcn/ui, Recharts         │       │   analytics · imports · sems · weather · ml · forecasts │
└──────────────────────────────┘       │   anomalies · reports · ai                             │
                                       │                                                        │
                                       │  services/        ml/           integrations/   ai/    │
                                       │  analytics        features      sems/client     tools  │
                                       │  energy_store     train         sems/adapter    analyst│
                                       │  reports          inference     sems/sync       providers
                                       │  bootstrap        anomaly       weather/open_meteo     │
                                       │                   explain       weather/sync           │
                                       │  ingestion/ readers · wide_reports · inspect ·         │
                                       │             normalize · quality · commit               │
                                       │  scheduler (APScheduler): SEMS poll, weather sync      │
                                       └───────────────┬────────────────────────────────────────┘
                                                       │ SQLAlchemy 2 + Alembic
                                       ┌───────────────▼───────────────┐    ┌──────────────────┐
                                       │ PostgreSQL 16/18              │    │ data/models/*.joblib │
                                       │ 17 tables, enum types,        │    │ (trained artifacts)  │
                                       │ NULLS NOT DISTINCT uniques    │    └──────────────────┘
                                       └───────────────────────────────┘
External: GoodWe SEMS+ (unofficial web API) · Open-Meteo (forecast, ERA5 archive, historical forecast) · any OpenAI-compatible LLM
```

## Major decisions

| Decision | Choice | Why |
|---|---|---|
| Data provenance | Every energy row carries `source` (`sems_api`, `csv_import`, `synthetic`); weather rows carry `kind` (`observed`/`forecast`) + `source` | The product must never present imported, synthetic or forecast data as live measurements. Analytics exclude synthetic rows unless explicitly requested. |
| Canonical daily table | `daily_energy` has one row per plant/date with source precedence API > import > synthetic enforced in `services/energy_store.py` (upsert `WHERE` clause) | Multiple sources cover the same days (API daily stats, 5-minute imports aggregated to days). Precedence keeps the best source without duplicates. |
| Interval readings | `energy_readings` keyed by `(plant_id, inverter_id, ts)` with `NULLS NOT DISTINCT` | Imports have no inverter id; PostgreSQL 15+ supports the constraint directly. |
| SEMS access | Unofficial SEMS+ web API (same protocol as the Home Assistant / pygoodwe / ioBroker integrations), verified live on 2026-11-11 | GoodWe's official OpenAPI is only for organisation accounts. The client is polite: ≥5 min polling, HTTP 429 / `GY0429` cool-down with backoff, one re-login on expired tokens, no scraping. |
| SEMS data model | Daily kWh from `stations/statistics`; live telemetry snapshot per poll; legacy endpoints are not used (they return empty for this account) | Intraday history is not exposed by the web API for household accounts; 5-minute history comes from the app's "Inverter Operation Report" export through the import pipeline, or from polling going forward. |
| Weather | Open-Meteo: ERA5 archive (observed, ~6 day delay) + historical-forecast API to bridge recent days + 16-day forecast API; tilted-plane irradiance (GTI) with plant tilt/azimuth | Free, no key, global, has GTI. Observed vs forecast stored separately with `issued_at` so forecast history is kept. |
| Ingestion | Column-mapping pipeline with inspection → user confirmation → quality report → commit. SEMS "wide" reports are unpivoted automatically. | No column names are assumed. Suspicious rows get quality flags and are never silently dropped; rejected rows are listed with reasons. |
| ML target | Next-day total kWh | Matches the data resolution (daily history) and the product question. |
| ML evaluation | Chronological hold-out + expanding-window CV; baselines evaluated on the same folds; selection by validation MAE with a documented weather-aware tie-break (10 % band) | Honest, leakage-free, and the anomaly detector needs a weather-adjusted expectation. |
| Forecast vs backtest | Backtests use observed weather; live forecasts use forecast weather and are scored separately (`live_forecast_mae_kwh`) | A backtest does not prove forecasting skill. |
| Anomalies | Residual z-scores vs validation sigma, coverage checks, intraday swing detector, Isolation Forest (secondary); every record has evidence + recommended checks; never a hardware verdict | Required by the product rules. |
| AI analyst | Provider-agnostic (`openai_compatible` default, Anthropic optional). Tools are backend functions scoped to the plant; `context` mode works for models without function calling | The owner will use a free LLM API; grounding must not depend on the provider. |
| Auth | Single bootstrap admin from env, JWT bearer, plant-level ownership checks | Household deployment; still real access control and multi-plant ready. |
| Scheduling | APScheduler inside the API process | No extra infrastructure; SEMS interval floor 300 s. |
| Packaging | uv (Python 3.12), npm, Docker Compose (db + backend + frontend), GitHub Actions CI | Simple, reproducible. Docker not available on the dev machine, so compose is written but untested there. |

## Data model (17 tables)

users, plants, inverters, energy_readings, daily_energy, weather_hourly, weather_daily, sync_logs,
import_jobs, model_versions, model_metrics, forecasts, anomalies, reports, conversations, messages, alembic_version.
See `backend/app/db/models.py` for columns and constraints; migrations in `backend/alembic/versions`.

## Request flow examples

* **SEMS sync** → `integrations/sems/sync.run_sems_sync` → client login (web mode) → station discovery → `stations/statistics` (daily) → `adapter.statistics_to_daily_rows` → `energy_store.upsert_daily_energy` → live telemetry → `EnergyReading` → `SyncLog`.
* **Import** → `POST /imports` (store file, `readers.load_table`, `inspect.profile_table`) → `POST /preview` (`normalize`, `quality`) → `POST /commit` (`commit_import` → energy_store).
* **Training** → `ml/train.train_models` → `features.build_training_frame` (daily + observed weather) → per-algorithm CV + test → artifacts in `data/models/plant_<id>/` → `ModelVersion`/`ModelMetric` rows → active model.
* **Forecast** → `ml/inference.issue_forecasts` → latest forecast weather per target date + lag features from actuals → `Forecast` rows with empirical interval and explanation.
* **Chat** → `ai/analyst.run_analyst_turn` → provider adapter → tool loop (`ai/tools.ToolExecutor`) → `Conversation`/`Message` with tool trace.

## Security notes

* Secrets only in `.env` (gitignored); `.env.example` documents them. Credentials never reach the frontend; SEMS status endpoints mask ids.
* Private exports live in `data/samples/private/` (gitignored); uploads in `data/uploads/` (gitignored).
* Input validation with Pydantic; file-type and size checks on upload; JWT expiry; safe error messages (unhandled exceptions return a generic 500).
