"""Backend-controlled tools exposed to the AI Energy Analyst.

Every tool is scoped to the plant the user is chatting about and reads through the
same service layer as the dashboard. The model never touches the database directly.
Results are JSON strings; errors are returned as error tool results by the loop.
Schemas use plain JSON Schema so they work with any provider's function calling.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, AnomalyStatus, ModelVersion, Plant, ReportKind, WeatherDaily, WeatherKind
from app.ml.inference import NoActiveModel, forecast_history, issue_forecasts
from app.services import analytics as an
from app.services.reports import build_report

MAX_SERIES_ITEMS = 400


def _schema(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_plant_info",
        "description": "Plant configuration: capacity, timezone, orientation, inverter, tariff, data coverage and which data sources are real vs demo.",
        "input_schema": _schema({}),
    },
    {
        "name": "get_generation_summary",
        "description": "Headline generation numbers: today (partial), yesterday, last 7/30 days, this/last month, lifetime, best day, specific yield, latest reading freshness, next-day forecast if available.",
        "input_schema": _schema({}),
    },
    {
        "name": "get_historical_generation",
        "description": "Daily generation (kWh) with observed weather per day for a date range, or monthly/weekly aggregates. Use for 'how much did I generate in <period>', trends, comparisons.",
        "input_schema": _schema(
            {
                "start_date": _DATE,
                "end_date": _DATE,
                "granularity": {
                    "type": "string",
                    "enum": ["daily", "weekly", "monthly"],
                    "description": "daily (default) returns per-day rows; monthly/weekly return aggregates for all history",
                },
            }
        ),
    },
    {
        "name": "get_best_days",
        "description": "The N highest (or lowest) generation days with their weather.",
        "input_schema": _schema(
            {
                "n": {"type": "integer", "minimum": 1, "maximum": 30},
                "lowest": {"type": "boolean", "description": "true for the worst days"},
            }
        ),
    },
    {
        "name": "get_forecast",
        "description": "Next-days generation forecasts (issued with weather forecasts) with intervals and explanation, past forecasts vs actuals and the live forecast error. Set refresh=true to issue a fresh forecast first.",
        "input_schema": _schema(
            {
                "refresh": {"type": "boolean"},
                "history_days": {"type": "integer", "minimum": 1, "maximum": 365},
            }
        ),
    },
    {
        "name": "get_anomalies",
        "description": "Detected anomalies (unexpected drops, zero days, missing data, fluctuations, underperformance) with evidence and recommended checks.",
        "input_schema": _schema(
            {
                "status": {"type": "string", "enum": ["open", "acknowledged", "resolved", "all"]},
                "start_date": _DATE,
                "end_date": _DATE,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }
        ),
    },
    {
        "name": "get_weather_summary",
        "description": "Observed daily weather (tilted irradiance, sunshine, cloud, temperature, rain) for a date range, the forward weather forecast, and generation-weather correlations.",
        "input_schema": _schema(
            {"start_date": _DATE, "end_date": _DATE, "include_forecast": {"type": "boolean"}}
        ),
    },
    {
        "name": "get_performance_metrics",
        "description": "Period comparisons (last 7 vs previous 7 days, month to date vs last month...), daily distribution statistics and specific yield. Use for 'is my system underperforming' together with get_anomalies and get_weather_summary.",
        "input_schema": _schema({}),
    },
    {
        "name": "get_model_metrics",
        "description": "Forecasting model registry: algorithms compared, validation/test MAE, RMSE, R2, selection policy, features and caveats about data size.",
        "input_schema": _schema({}),
    },
    {
        "name": "get_energy_savings",
        "description": "Estimated financial value of generation for a period using the configured tariff. Explains missing inputs (tariff, meter data) instead of guessing.",
        "input_schema": _schema({"start_date": _DATE, "end_date": _DATE}),
    },
    {
        "name": "get_intraday_power",
        "description": "5-minute / live power readings for one day (when interval data exists) with hourly irradiance, to inspect the shape of a day's power curve.",
        "input_schema": _schema({"day": _DATE}, ["day"]),
    },
    {
        "name": "generate_performance_report",
        "description": "Build a daily, weekly or monthly performance report (generation, weather context, anomalies, forecast performance, savings). Returns the structured report.",
        "input_schema": _schema(
            {
                "kind": {"type": "string", "enum": ["daily", "weekly", "monthly"]},
                "anchor_date": {
                    "type": "string",
                    "description": "A date inside the period; defaults to the current period",
                },
            },
            ["kind"],
        ),
    },
]
TOOL_NAMES = [t["name"] for t in TOOL_DEFINITIONS]


def _parse_date(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def _trim(items: list[Any], n: int = MAX_SERIES_ITEMS) -> list[Any]:
    return items[-n:] if len(items) > n else items


class ToolExecutor:
    def __init__(self, db: Session, plant: Plant):
        self.db = db
        self.plant = plant
        self.calls: list[dict[str, Any]] = []

    def run(self, name: str, args: dict[str, Any] | None) -> tuple[str, bool]:
        """Returns (json_result, is_error)."""
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            self.calls.append({"tool": name, "input": args, "ok": False, "error": "unknown tool"})
            return json.dumps({"error": f"Unknown tool {name}", "available": TOOL_NAMES}), True
        args = {k: v for k, v in (args or {}).items() if not k.startswith("_")}
        try:
            result = fn(**args)
            payload = json.dumps(result, default=str)
            self.calls.append({"tool": name, "input": args, "ok": True, "chars": len(payload)})
            return payload, False
        except NoActiveModel as exc:
            self.calls.append({"tool": name, "input": args, "ok": False, "error": str(exc)})
            return json.dumps({"error": str(exc), "hint": "No forecasting model has been trained yet"}), True
        except TypeError as exc:
            self.calls.append({"tool": name, "input": args, "ok": False, "error": f"bad arguments: {exc}"})
            return json.dumps({"error": f"Invalid arguments for {name}: {exc}"}), True
        except Exception as exc:
            self.calls.append(
                {"tool": name, "input": args, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
            )
            return json.dumps({"error": f"{type(exc).__name__}: {exc}"}), True

    # ------------------------------------------------------------------ tools

    def tool_get_plant_info(self) -> dict[str, Any]:
        p = self.plant
        cov = an.daily_frame(self.db, p, include_synthetic=True)
        sources = sorted(cov["source"].unique().tolist()) if not cov.empty else []
        return {
            "name": p.name,
            "capacity_kw": p.capacity_kw,
            "timezone": p.timezone,
            "panel_tilt_deg": p.panel_tilt_deg,
            "panel_azimuth_deg": p.panel_azimuth_deg,
            "orientation_note": "azimuth 0=south, 90=west, -90=east",
            "installation_date": p.installation_date.isoformat() if p.installation_date else None,
            "inverter": {
                "manufacturer": p.inverter_manufacturer,
                "model": p.inverter_model,
                "count": len(p.inverters),
            },
            "monitoring_platform": p.monitoring_platform,
            "currency": p.currency,
            "tariff_import_per_kwh": p.tariff_import_per_kwh,
            "tariff_export_per_kwh": p.tariff_export_per_kwh,
            "installation_cost": p.installation_cost,
            "is_demo_plant": p.is_demo,
            "data_sources_present": sources,
            "data_days": int(len(cov)),
            "data_range": {"first": str(cov["date"].min()), "last": str(cov["date"].max())}
            if not cov.empty
            else None,
            "smart_meter": False if "no_smart_meter" in str(cov.get("quality_flags", "")) else None,
            "notes": p.notes,
        }

    def tool_get_generation_summary(self) -> dict[str, Any]:
        return an.summary(self.db, self.plant)

    def tool_get_historical_generation(
        self, start_date: str | None = None, end_date: str | None = None, granularity: str = "daily"
    ) -> dict[str, Any]:
        if granularity == "monthly":
            return an.monthly_series(self.db, self.plant)
        if granularity == "weekly":
            return an.weekly_series(self.db, self.plant, 52)
        end = _parse_date(end_date) or an.plant_today(self.plant)
        start = _parse_date(start_date) or end - timedelta(days=30)
        res = an.daily_series(self.db, self.plant, start, end)
        keep = (
            "date",
            "generation_kwh",
            "source",
            "is_complete",
            "gti_wh",
            "sunshine_s",
            "cloud_cover",
            "temp_max",
            "precip",
            "quality_flags",
        )
        res["items"] = _trim([{k: i.get(k) for k in keep} for i in res["items"]])
        return res

    def tool_get_best_days(self, n: int = 5, lowest: bool = False) -> dict[str, Any]:
        df = an.daily_frame(self.db, self.plant)
        df = df[df["is_complete"]]
        if df.empty:
            return {"items": [], "note": "No complete days available"}
        w = an.weather_daily_frame(self.db, self.plant, None, None)
        merged = df.merge(w, on="date", how="left").sort_values("generation_kwh", ascending=lowest).head(n)
        return {
            "lowest": lowest,
            "items": an._records(
                merged[["date", "generation_kwh", "gti_wh", "cloud_cover", "temp_max", "precip", "source"]]
            ),
        }

    def tool_get_forecast(self, refresh: bool = False, history_days: int = 30) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if refresh:
            out["issued_now"] = issue_forecasts(self.db, self.plant, days=3)
        history = forecast_history(self.db, self.plant, history_days)
        today = an.plant_today(self.plant)
        out["upcoming"] = [h for h in history if date.fromisoformat(h["target_date"]) > today]
        past = [h for h in history if h["actual_kwh"] is not None]
        out["past_with_actuals"] = _trim(past, 60)
        out["live_forecast_mae_kwh"] = (
            round(sum(abs(h["error_kwh"]) for h in past) / len(past), 2) if past else None
        )
        out["scored_days"] = len(past)
        mv = self.db.scalar(
            select(ModelVersion).where(
                ModelVersion.plant_id == self.plant.id, ModelVersion.is_active.is_(True)
            )
        )
        out["active_model"] = (
            {
                "algorithm": mv.algorithm,
                "validation_mae": (mv.metrics or {}).get("validation", {}).get("mae"),
                "test_mae": (mv.metrics or {}).get("test", {}).get("mae"),
            }
            if mv
            else None
        )
        if not out["upcoming"]:
            out["note"] = (
                "No upcoming forecast stored; call with refresh=true to issue one (requires an active model and weather forecast)."
            )
        return out

    def tool_get_anomalies(
        self,
        status: str = "open",
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        stmt = select(Anomaly).where(Anomaly.plant_id == self.plant.id)
        if status and status != "all":
            stmt = stmt.where(Anomaly.status == AnomalyStatus(status))
        if start_date:
            stmt = stmt.where(Anomaly.date >= _parse_date(start_date))
        if end_date:
            stmt = stmt.where(Anomaly.date <= _parse_date(end_date))
        rows = self.db.scalars(stmt.order_by(desc(Anomaly.date)).limit(limit)).all()
        return {
            "count": len(rows),
            "items": [
                {
                    "date": a.date.isoformat(),
                    "category": a.category,
                    "severity": a.severity.value,
                    "status": a.status.value,
                    "title": a.title,
                    "description": a.description,
                    "actual_kwh": a.actual_kwh,
                    "expected_kwh": round(a.expected_kwh, 2) if a.expected_kwh is not None else None,
                    "deviation_pct": a.deviation_pct,
                    "weather": (a.evidence or {}).get("weather"),
                    "neighbouring_days_kwh": (a.evidence or {}).get("neighbouring_days_kwh"),
                    "recommended_checks": a.recommended_checks,
                }
                for a in rows
            ],
        }

    def tool_get_weather_summary(
        self, start_date: str | None = None, end_date: str | None = None, include_forecast: bool = True
    ) -> dict[str, Any]:
        end = _parse_date(end_date) or an.plant_today(self.plant)
        start = _parse_date(start_date) or end - timedelta(days=14)
        w = an.weather_daily_frame(self.db, self.plant, start, end)
        out: dict[str, Any] = {
            "observed": _trim(an._records(w)),
            "correlations": an.weather_correlation(self.db, self.plant, start - timedelta(days=60), end)[
                "correlations"
            ],
        }
        if include_forecast:
            rows = self.db.scalars(
                select(WeatherDaily)
                .where(
                    WeatherDaily.plant_id == self.plant.id,
                    WeatherDaily.kind == WeatherKind.FORECAST,
                    WeatherDaily.date > an.plant_today(self.plant),
                )
                .order_by(WeatherDaily.date, desc(WeatherDaily.issued_at))
            ).all()
            seen: dict[date, Any] = {}
            for r in rows:
                seen.setdefault(
                    r.date,
                    {
                        "date": r.date.isoformat(),
                        "gti_wh": r.gti_sum_wh_m2,
                        "cloud_cover": r.cloud_cover_mean,
                        "temp_max": r.temperature_max,
                        "precip": r.precipitation_sum,
                        "issued_at": r.issued_at.isoformat() if r.issued_at else None,
                    },
                )
            out["forecast"] = list(seen.values())[:7]
        return out

    def tool_get_performance_metrics(self) -> dict[str, Any]:
        today = an.plant_today(self.plant)
        return {
            "comparisons": an.compare_periods(self.db, self.plant)["periods"],
            "distribution": an.distribution(self.db, self.plant, today - timedelta(days=365), today)["stats"],
            "specific_yield_30d_kwh_per_kw": an.summary(self.db, self.plant)["specific_yield_30d_kwh_per_kw"],
            "capacity_kw": self.plant.capacity_kw,
            "note": "Specific yield = kWh per kW installed per day; compare across periods, not against other sites without weather context.",
        }

    def tool_get_model_metrics(self) -> dict[str, Any]:
        rows = self.db.scalars(
            select(ModelVersion)
            .where(ModelVersion.plant_id == self.plant.id)
            .order_by(desc(ModelVersion.trained_at))
            .limit(14)
        ).all()
        if not rows:
            return {"models": [], "note": "No models trained yet"}
        latest_prefix = rows[0].version_tag.rsplit("_", 1)[0]
        latest = [m for m in rows if m.version_tag.startswith(latest_prefix)]
        return {
            "trained_at": rows[0].trained_at.isoformat(),
            "rows_used": rows[0].row_counts,
            "data_sources": rows[0].data_sources,
            "models": [
                {
                    "algorithm": m.algorithm,
                    "is_active": m.is_active,
                    "is_baseline": m.is_baseline,
                    "validation": (m.metrics or {}).get("validation"),
                    "test": (m.metrics or {}).get("test"),
                    "top_features": list((m.feature_importance or {}).items())[:5],
                }
                for m in latest
            ],
            "caveats": rows[0].notes,
        }

    def tool_get_energy_savings(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> dict[str, Any]:
        return an.savings(self.db, self.plant, _parse_date(start_date), _parse_date(end_date))

    def tool_get_intraday_power(self, day: str) -> dict[str, Any]:
        res = an.intraday(self.db, self.plant, _parse_date(day))
        readings = res["readings"]
        if not readings:
            return {
                "day": day,
                "readings": [],
                "note": "No interval readings for this day (only daily totals). Import the SEMS operation report for 5-minute data.",
            }
        powered = [r for r in readings if r["power_w"] is not None]
        peak = max(powered, key=lambda r: r["power_w"]) if powered else None
        return {
            "day": day,
            "reading_count": len(readings),
            "sources": res["sources"],
            "peak": {"local_time": peak["local_time"], "power_w": peak["power_w"]} if peak else None,
            "first_generation_time": next((r["local_time"] for r in powered if r["power_w"] > 50), None),
            "last_generation_time": next(
                (r["local_time"] for r in reversed(powered) if r["power_w"] > 50), None
            ),
            "sample_every_30min": [{"t": r["local_time"], "w": r["power_w"]} for r in readings[::6]][:60],
            "weather_hourly": [
                {"t": w["local_time"], "ghi": w["ghi"], "gti": w["gti"], "cloud": w["cloud_cover"]}
                for w in res["weather_hourly"]
            ],
        }

    def tool_generate_performance_report(self, kind: str, anchor_date: str | None = None) -> dict[str, Any]:
        built = build_report(self.db, self.plant, ReportKind(kind), _parse_date(anchor_date))
        content = dict(built["content"])
        content["daily"] = _trim(content.get("daily", []), 62)
        return {"title": built["title"], "summary_text": built["summary_text"], "content": content}
