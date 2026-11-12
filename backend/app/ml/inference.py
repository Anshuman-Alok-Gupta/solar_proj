"""Inference: expected generation for past days (observed weather) and forecasts for future days
(forecast weather), with empirical prediction intervals and SHAP explanations."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import DailyEnergy, DataSource, Forecast, ModelVersion, Plant, WeatherDaily, WeatherKind
from app.ml.explain import explain_row
from app.ml.features import TARGET, add_calendar, add_lags, weather_rows_to_frame
from app.ml.registry import ArtifactMissing, get_active, load_artifact
from app.services.analytics import plant_today

log = logging.getLogger(__name__)

WEATHER_COLS = [
    WeatherDaily.date,
    WeatherDaily.shortwave_radiation_sum,
    WeatherDaily.gti_sum_wh_m2,
    WeatherDaily.sunshine_duration_s,
    WeatherDaily.cloud_cover_mean,
    WeatherDaily.temperature_max,
    WeatherDaily.temperature_mean,
    WeatherDaily.humidity_mean,
    WeatherDaily.wind_speed_max,
    WeatherDaily.precipitation_sum,
    WeatherDaily.source,
    WeatherDaily.issued_at,
]


class NoActiveModel(RuntimeError):
    pass


def _load_active_artifact(mv: ModelVersion) -> dict[str, Any]:
    try:
        return load_artifact(mv)
    except ArtifactMissing as exc:
        raise NoActiveModel(f"{exc}") from exc


def _history(db: Session, plant: Plant, *, include_synthetic: bool = False) -> pd.DataFrame:
    sources = [DataSource.SEMS_API, DataSource.CSV_IMPORT] + (
        [DataSource.SYNTHETIC] if include_synthetic else []
    )
    rows = db.execute(
        select(DailyEnergy.date, DailyEnergy.generation_kwh, DailyEnergy.is_complete)
        .where(DailyEnergy.plant_id == plant.id, DailyEnergy.source.in_(sources))
        .order_by(DailyEnergy.date)
    ).all()
    df = pd.DataFrame(rows, columns=["date", TARGET, "is_complete"])
    return df[df["is_complete"]].drop(columns=["is_complete"]) if not df.empty else df


def observed_weather(db: Session, plant: Plant) -> pd.DataFrame:
    rows = (
        db.execute(
            select(*WEATHER_COLS).where(
                WeatherDaily.plant_id == plant.id, WeatherDaily.kind == WeatherKind.OBSERVED
            )
        )
        .mappings()
        .all()
    )
    return weather_rows_to_frame([dict(r) for r in rows])


def latest_forecast_weather(
    db: Session, plant: Plant, start: date, end: date
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Latest issued forecast per target date in [start, end]."""
    rows = (
        db.execute(
            select(*WEATHER_COLS)
            .where(
                WeatherDaily.plant_id == plant.id,
                WeatherDaily.kind == WeatherKind.FORECAST,
                WeatherDaily.date >= start,
                WeatherDaily.date <= end,
            )
            .order_by(WeatherDaily.date, desc(WeatherDaily.issued_at))
        )
        .mappings()
        .all()
    )
    seen: dict[date, dict] = {}
    for r in rows:
        if r["date"] not in seen:
            seen[r["date"]] = dict(r)
    meta = {
        "issued_at": max((r["issued_at"] for r in seen.values()), default=None),
        "source": next(iter(seen.values()))["source"] if seen else None,
    }
    return weather_rows_to_frame(list(seen.values())), meta


def _feature_matrix(
    history: pd.DataFrame,
    weather: pd.DataFrame,
    targets: list[date],
    latitude: float | None,
    features: list[str],
) -> pd.DataFrame:
    """Rows for `targets` where lag features come only from history strictly before each date."""
    base = history.merge(weather, on="date", how="outer")
    extra = pd.DataFrame({"date": [t for t in targets if t not in set(base["date"])]})
    if not extra.empty:
        base = pd.concat([base, extra], ignore_index=True)
    # inject target-day weather (may be forecast) without touching history rows
    base = base.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    w_idx = weather.set_index("date")
    for t in targets:
        if t in w_idx.index:
            for c in w_idx.columns:
                base.loc[base["date"] == t, c] = w_idx.loc[t, c]
    base = add_calendar(base, latitude)
    base = add_lags(base)
    out = base[base["date"].isin(targets)].copy()
    for f in features:
        if f not in out.columns:
            out[f] = np.nan
    return out.set_index("date")[features]


def explain_prediction(artifact: dict[str, Any], X_row: pd.DataFrame) -> dict[str, Any] | None:
    """SHAP for tree models; a transparent rule explanation for baselines."""
    algo = artifact["algorithm"]
    row = X_row.iloc[0]

    def val(name: str):
        v = row.get(name)
        return None if v is None or pd.isna(v) else round(float(v), 3)

    if algo == "baseline_irradiance":
        pr = val("pr_roll7")
        model = artifact["model"]
        pr_used = pr if pr is not None else round(float(getattr(model, "global_pr_", 0.0)), 3)
        return {
            "method": "performance ratio x tilted irradiance",
            "formula": "kWh = PR_recent x GTI(Wh/m2) / 1000",
            "top_contributions": [
                {
                    "feature": "gti_wh",
                    "label": "Tilted-plane irradiance (Wh/m²)",
                    "value": val("gti_wh"),
                    "contribution_kwh": None,
                },
                {
                    "feature": "pr_roll7",
                    "label": "Recent performance ratio (kWh per kWh/m²)",
                    "value": pr_used,
                    "contribution_kwh": None,
                },
            ],
        }
    if algo == "baseline_rolling7":
        return {
            "method": "7-day mean of recent generation",
            "top_contributions": [
                {
                    "feature": "gen_roll7_mean",
                    "label": "7-day mean generation",
                    "value": val("gen_roll7_mean"),
                    "contribution_kwh": None,
                }
            ],
        }
    if algo == "baseline_persistence":
        return {
            "method": "yesterday's generation",
            "top_contributions": [
                {
                    "feature": "gen_lag1",
                    "label": "Yesterday's generation",
                    "value": val("gen_lag1"),
                    "contribution_kwh": None,
                }
            ],
        }
    return explain_row(artifact, X_row)


def _interval(artifact: dict[str, Any], pred: float) -> tuple[float | None, float | None]:
    q = artifact.get("residual_quantiles")
    if not q:
        return None, None
    lo = pred + q.get(0.1, q.get("0.1", 0.0))
    hi = pred + q.get(0.9, q.get("0.9", 0.0))
    return round(max(0.0, lo), 2), round(max(0.0, hi), 2)


def expected_for_dates(
    db: Session, plant: Plant, dates: list[date], *, model: ModelVersion | None = None
) -> dict[str, Any]:
    """Weather-adjusted expected generation for past dates using OBSERVED weather."""
    mv = model or get_active(db, plant.id)
    if mv is None:
        raise NoActiveModel("No active model; train one first")
    art = _load_active_artifact(mv)
    hist = _history(db, plant)
    weather = observed_weather(db, plant)
    X = _feature_matrix(hist, weather, dates, plant.latitude, art["features"])
    preds = art["model"].predict(X)
    return {
        "model_version_id": mv.id,
        "algorithm": art["algorithm"],
        "residual_std": art.get("residual_std"),
        "expected": {d: float(p) for d, p in zip(X.index, preds, strict=True)},
        "features": X,
    }


def issue_forecasts(
    db: Session, plant: Plant, *, days: int = 3, model: ModelVersion | None = None
) -> dict[str, Any]:
    mv = model or get_active(db, plant.id)
    if mv is None:
        raise NoActiveModel("No active model; train one first")
    art = _load_active_artifact(mv)
    today = plant_today(plant)
    targets = [today + timedelta(days=i) for i in range(1, days + 1)]
    hist = _history(db, plant)
    obs = observed_weather(db, plant)
    fc, meta = latest_forecast_weather(db, plant, targets[0], targets[-1])
    notes: list[str] = []
    if fc.empty:
        return {
            "issued": [],
            "notes": ["No weather forecast stored for the target dates; run a weather sync first"],
            "model_version_id": mv.id,
        }
    available = [t for t in targets if t in set(fc["date"])]
    missing = [t for t in targets if t not in set(fc["date"])]
    if missing:
        notes.append(f"No forecast weather for {', '.join(d.isoformat() for d in missing)}")
    # history weather = observed; target weather = forecast
    weather = pd.concat(
        [obs[~obs["date"].isin(available)], fc[fc["date"].isin(available)]], ignore_index=True
    )
    X = _feature_matrix(hist, weather, available, plant.latitude, art["features"])
    preds = art["model"].predict(X)
    issued_at = datetime.now(UTC)
    out = []
    for t, p in zip(X.index, preds, strict=True):
        p = float(max(0.0, p))
        lo, hi = _interval(art, p)
        expl = explain_prediction(art, X.loc[[t]])
        row = {
            "plant_id": plant.id,
            "model_version_id": mv.id,
            "target_date": t,
            "issued_at": issued_at,
            "horizon_days": (t - today).days,
            "predicted_kwh": round(p, 3),
            "lower_kwh": lo,
            "upper_kwh": hi,
            "weather_input_kind": "forecast",
            "inputs": {k: (None if pd.isna(v) else round(float(v), 4)) for k, v in X.loc[t].items()}
            | {
                "weather_source": meta["source"],
                "weather_issued_at": meta["issued_at"].isoformat() if meta["issued_at"] else None,
            },
            "explanation": expl,
        }
        db.execute(pg_insert(Forecast).values(**row).on_conflict_do_nothing())
        out.append({**row, "target_date": t.isoformat(), "issued_at": issued_at.isoformat()})
    db.commit()
    if art.get("residual_quantiles") is None:
        notes.append("Prediction intervals unavailable (too few validation residuals)")
    else:
        notes.append(
            "Interval = 10th-90th percentile of validation residuals added to the point forecast (empirical, not a guarantee)"
        )
    notes.append(
        "Inputs are Open-Meteo forecast weather; accuracy depends on the weather forecast, which backtests do not capture"
    )
    return {"model_version_id": mv.id, "algorithm": art["algorithm"], "issued": out, "notes": notes}


def backfill_actuals(db: Session, plant: Plant) -> int:
    rows = db.scalars(
        select(Forecast).where(Forecast.plant_id == plant.id, Forecast.actual_kwh.is_(None))
    ).all()
    if not rows:
        return 0
    hist = _history(db, plant).set_index("date")[TARGET]
    n = 0
    for f in rows:
        if f.target_date in hist.index:
            f.actual_kwh = float(hist.loc[f.target_date])
            n += 1
    db.commit()
    return n


def forecast_history(db: Session, plant: Plant, limit_days: int = 60) -> list[dict[str, Any]]:
    """Latest forecast per target date (1-day horizon preferred) with actuals."""
    since = plant_today(plant) - timedelta(days=limit_days)
    rows = db.scalars(
        select(Forecast)
        .where(Forecast.plant_id == plant.id, Forecast.target_date >= since)
        .order_by(Forecast.target_date, Forecast.horizon_days, desc(Forecast.issued_at))
    ).all()
    seen: dict[date, Forecast] = {}
    for f in rows:
        seen.setdefault(f.target_date, f)
    return [
        {
            "target_date": f.target_date.isoformat(),
            "issued_at": f.issued_at.isoformat(),
            "horizon_days": f.horizon_days,
            "predicted_kwh": f.predicted_kwh,
            "lower_kwh": f.lower_kwh,
            "upper_kwh": f.upper_kwh,
            "actual_kwh": f.actual_kwh,
            "error_kwh": round(f.actual_kwh - f.predicted_kwh, 3) if f.actual_kwh is not None else None,
            "weather_input_kind": f.weather_input_kind,
            "model_version_id": f.model_version_id,
        }
        for f in seen.values()
    ]
