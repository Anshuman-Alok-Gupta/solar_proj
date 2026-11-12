"""Analytics over stored energy + weather data (no fabrication: every number is derived from rows).

Conventions
-----------
* "today" is the plant's local civil date.
* Rows with is_complete=False (today's partial value) are labelled, never silently mixed in.
* Synthetic rows are excluded unless ``include_synthetic`` is set, and the response says so.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Anomaly,
    AnomalyStatus,
    DailyEnergy,
    DataSource,
    EnergyReading,
    Forecast,
    Plant,
    SyncLog,
    SyncStatus,
    WeatherDaily,
    WeatherHourly,
    WeatherKind,
)


def plant_today(plant: Plant) -> date:
    return datetime.now(ZoneInfo(plant.timezone or "UTC")).date()


def _sources(include_synthetic: bool) -> list[DataSource]:
    s = [DataSource.SEMS_API, DataSource.CSV_IMPORT]
    if include_synthetic:
        s.append(DataSource.SYNTHETIC)
    return s


def daily_frame(
    db: Session,
    plant: Plant,
    start: date | None = None,
    end: date | None = None,
    *,
    include_synthetic: bool = False,
) -> pd.DataFrame:
    stmt = select(
        DailyEnergy.date,
        DailyEnergy.generation_kwh,
        DailyEnergy.peak_power_w,
        DailyEnergy.consumption_kwh,
        DailyEnergy.self_consumed_kwh,
        DailyEnergy.exported_kwh,
        DailyEnergy.imported_kwh,
        DailyEnergy.source,
        DailyEnergy.is_complete,
        DailyEnergy.derived_from,
        DailyEnergy.quality_flags,
    ).where(DailyEnergy.plant_id == plant.id, DailyEnergy.source.in_(_sources(include_synthetic)))
    if start:
        stmt = stmt.where(DailyEnergy.date >= start)
    if end:
        stmt = stmt.where(DailyEnergy.date <= end)
    rows = db.execute(stmt.order_by(DailyEnergy.date)).all()
    df = pd.DataFrame(
        rows,
        columns=[
            "date",
            "generation_kwh",
            "peak_power_w",
            "consumption_kwh",
            "self_consumed_kwh",
            "exported_kwh",
            "imported_kwh",
            "source",
            "is_complete",
            "derived_from",
            "quality_flags",
        ],
    )
    if not df.empty:
        df["source"] = df["source"].map(lambda s: s.value if hasattr(s, "value") else s)
    return df


def weather_daily_frame(db: Session, plant: Plant, start: date | None, end: date | None) -> pd.DataFrame:
    stmt = select(
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
        WeatherDaily.weather_code,
        WeatherDaily.source,
    ).where(WeatherDaily.plant_id == plant.id, WeatherDaily.kind == WeatherKind.OBSERVED)
    if start:
        stmt = stmt.where(WeatherDaily.date >= start)
    if end:
        stmt = stmt.where(WeatherDaily.date <= end)
    rows = db.execute(stmt.order_by(WeatherDaily.date)).all()
    return pd.DataFrame(
        rows,
        columns=[
            "date",
            "ghi_mj",
            "gti_wh",
            "sunshine_s",
            "cloud_cover",
            "temp_max",
            "temp_mean",
            "humidity",
            "wind_max",
            "precip",
            "weather_code",
            "weather_source",
        ],
    )


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.copy()
    for c in out.columns:
        if c == "date" or str(out[c].dtype).startswith("datetime"):
            out[c] = out[c].astype(str)
    out = out.astype(object).where(pd.notna(out), None)
    return out.to_dict(orient="records")


# --------------------------------------------------------------------------- summary


def _latest_reading(db: Session, plant: Plant) -> EnergyReading | None:
    return db.scalar(
        select(EnergyReading)
        .where(EnergyReading.plant_id == plant.id, EnergyReading.source != DataSource.SYNTHETIC)
        .order_by(desc(EnergyReading.ts))
        .limit(1)
    )


def summary(
    db: Session, plant: Plant, *, include_synthetic: bool = False, live_threshold_minutes: int = 30
) -> dict[str, Any]:
    today = plant_today(plant)
    df = daily_frame(db, plant, include_synthetic=include_synthetic)
    complete = df[df["is_complete"]] if not df.empty else df
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    def total(frame: pd.DataFrame, a: date, b: date) -> float | None:
        if frame.empty:
            return None
        sel = frame[(frame["date"] >= a) & (frame["date"] <= b)]
        return round(float(sel["generation_kwh"].sum()), 2) if not sel.empty else None

    today_row = df[df["date"] == today] if not df.empty else df
    yesterday_row = df[df["date"] == today - timedelta(days=1)] if not df.empty else df
    last7 = complete[complete["date"] >= today - timedelta(days=7)] if not complete.empty else complete
    last30 = complete[complete["date"] >= today - timedelta(days=30)] if not complete.empty else complete
    best = complete.loc[complete["generation_kwh"].idxmax()] if not complete.empty else None

    latest = _latest_reading(db, plant)
    now = datetime.now(UTC)
    latest_out = None
    if latest is not None:
        age_min = (now - latest.ts).total_seconds() / 60
        latest_out = {
            "ts": latest.ts.isoformat(),
            "power_w": latest.power_w,
            "temperature_c": latest.temperature_c,
            "source": latest.source.value,
            "age_minutes": round(age_min),
            "is_live": age_min <= live_threshold_minutes,
        }
    last_sync_ok = db.scalar(
        select(SyncLog)
        .where(SyncLog.plant_id == plant.id, SyncLog.source == "sems", SyncLog.status == SyncStatus.SUCCESS)
        .order_by(desc(SyncLog.finished_at))
        .limit(1)
    )
    open_anoms = db.scalar(
        select(func.count()).where(Anomaly.plant_id == plant.id, Anomaly.status == AnomalyStatus.OPEN)
    )
    next_fc = db.scalar(
        select(Forecast)
        .where(Forecast.plant_id == plant.id, Forecast.target_date == today + timedelta(days=1))
        .order_by(desc(Forecast.issued_at))
        .limit(1)
    )
    specific_yield_30d = None
    if plant.capacity_kw and not last30.empty:
        specific_yield_30d = round(float(last30["generation_kwh"].mean()) / plant.capacity_kw, 2)
    return {
        "plant_id": plant.id,
        "today": today.isoformat(),
        "timezone": plant.timezone,
        "capacity_kw": plant.capacity_kw,
        "includes_synthetic": include_synthetic,
        "has_data": not df.empty,
        "data_range": {"first": str(df["date"].min()), "last": str(df["date"].max()), "days": int(len(df))}
        if not df.empty
        else None,
        "today_kwh": round(float(today_row["generation_kwh"].iloc[0]), 2) if not today_row.empty else None,
        "today_is_partial": bool(not today_row.empty and not today_row["is_complete"].iloc[0]),
        "yesterday_kwh": round(float(yesterday_row["generation_kwh"].iloc[0]), 2)
        if not yesterday_row.empty
        else None,
        "last_7_days_kwh": round(float(last7["generation_kwh"].sum()), 2) if not last7.empty else None,
        "last_30_days_kwh": round(float(last30["generation_kwh"].sum()), 2) if not last30.empty else None,
        "avg_daily_30d_kwh": round(float(last30["generation_kwh"].mean()), 2) if not last30.empty else None,
        "specific_yield_30d_kwh_per_kw": specific_yield_30d,
        "this_month_kwh": total(df, month_start, today),
        "last_month_kwh": total(df, last_month_start, last_month_end),
        "lifetime_kwh": round(float(df["generation_kwh"].sum()), 2) if not df.empty else None,
        "best_day": {"date": str(best["date"]), "kwh": round(float(best["generation_kwh"]), 2)}
        if best is not None
        else None,
        "latest_reading": latest_out,
        "last_successful_sems_sync": last_sync_ok.finished_at.isoformat()
        if last_sync_ok and last_sync_ok.finished_at
        else None,
        "open_anomalies": int(open_anoms or 0),
        "next_day_forecast": {
            "target_date": next_fc.target_date.isoformat(),
            "predicted_kwh": round(next_fc.predicted_kwh, 2),
            "lower_kwh": next_fc.lower_kwh,
            "upper_kwh": next_fc.upper_kwh,
            "weather_input_kind": next_fc.weather_input_kind,
            "issued_at": next_fc.issued_at.isoformat(),
        }
        if next_fc
        else None,
    }


# --------------------------------------------------------------------------- series


def daily_series(
    db: Session, plant: Plant, start: date, end: date, *, include_synthetic: bool = False
) -> dict[str, Any]:
    df = daily_frame(db, plant, start, end, include_synthetic=include_synthetic)
    w = weather_daily_frame(db, plant, start, end)
    merged = df.merge(w, on="date", how="left") if not df.empty else df
    if not merged.empty and plant.capacity_kw:
        merged["specific_yield"] = (merged["generation_kwh"] / plant.capacity_kw).round(3)
    return {
        "plant_id": plant.id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "count": int(len(merged)),
        "total_kwh": round(float(merged["generation_kwh"].sum()), 2) if not merged.empty else 0.0,
        "items": _records(merged),
    }


def monthly_series(db: Session, plant: Plant, *, include_synthetic: bool = False) -> dict[str, Any]:
    df = daily_frame(db, plant, include_synthetic=include_synthetic)
    if df.empty:
        return {"plant_id": plant.id, "items": []}
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    g = (
        df.groupby("month")
        .agg(
            total_kwh=("generation_kwh", "sum"),
            days_with_data=("date", "count"),
            avg_daily_kwh=("generation_kwh", "mean"),
            best_day_kwh=("generation_kwh", "max"),
            worst_day_kwh=("generation_kwh", "min"),
        )
        .reset_index()
    )
    g["days_in_month"] = g["month"].map(lambda m: monthrange(int(m[:4]), int(m[5:7]))[1])
    g["coverage"] = (g["days_with_data"] / g["days_in_month"]).round(3)
    if plant.capacity_kw:
        g["specific_yield_kwh_per_kw"] = (g["total_kwh"] / plant.capacity_kw).round(2)
    g = g.round(2)
    return {"plant_id": plant.id, "items": _records(g)}


def weekly_series(
    db: Session, plant: Plant, weeks: int = 26, *, include_synthetic: bool = False
) -> dict[str, Any]:
    today = plant_today(plant)
    df = daily_frame(db, plant, today - timedelta(weeks=weeks), today, include_synthetic=include_synthetic)
    if df.empty:
        return {"plant_id": plant.id, "items": []}
    d = pd.to_datetime(df["date"])
    df["week_start"] = (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.date.astype(str)
    g = (
        df.groupby("week_start")
        .agg(
            total_kwh=("generation_kwh", "sum"),
            days_with_data=("date", "count"),
            avg_daily_kwh=("generation_kwh", "mean"),
        )
        .reset_index()
        .round(2)
    )
    return {"plant_id": plant.id, "items": _records(g)}


def distribution(
    db: Session, plant: Plant, start: date, end: date, bins: int = 12, *, include_synthetic: bool = False
) -> dict[str, Any]:
    df = daily_frame(db, plant, start, end, include_synthetic=include_synthetic)
    df = df[df["is_complete"]] if not df.empty else df
    if df.empty or len(df) < 2:
        return {"plant_id": plant.id, "count": int(len(df)), "bins": [], "stats": None}
    vals = df["generation_kwh"].to_numpy()
    counts, edges = np.histogram(vals, bins=bins)
    return {
        "plant_id": plant.id,
        "count": int(len(vals)),
        "bins": [
            {
                "from_kwh": round(float(edges[i]), 2),
                "to_kwh": round(float(edges[i + 1]), 2),
                "days": int(counts[i]),
            }
            for i in range(len(counts))
        ],
        "stats": {
            "mean": round(float(vals.mean()), 2),
            "median": round(float(np.median(vals)), 2),
            "std": round(float(vals.std(ddof=1)), 2),
            "p10": round(float(np.percentile(vals, 10)), 2),
            "p90": round(float(np.percentile(vals, 90)), 2),
            "min": round(float(vals.min()), 2),
            "max": round(float(vals.max()), 2),
        },
    }


def weather_correlation(
    db: Session, plant: Plant, start: date, end: date, *, include_synthetic: bool = False
) -> dict[str, Any]:
    df = daily_frame(db, plant, start, end, include_synthetic=include_synthetic)
    df = df[df["is_complete"]] if not df.empty else df
    w = weather_daily_frame(db, plant, start, end)
    merged = df.merge(w, on="date", how="inner") if not df.empty else pd.DataFrame()
    if merged.empty:
        return {
            "plant_id": plant.id,
            "count": 0,
            "correlations": {},
            "items": [],
            "note": "No overlapping generation and weather days",
        }
    corr: dict[str, float | None] = {}
    for col in ("gti_wh", "ghi_mj", "sunshine_s", "cloud_cover", "temp_max", "humidity", "precip"):
        sub = merged[["generation_kwh", col]].dropna()
        corr[col] = (
            round(float(sub["generation_kwh"].corr(sub[col])), 3)
            if len(sub) >= 5 and sub[col].std() > 0
            else None
        )
    return {
        "plant_id": plant.id,
        "count": int(len(merged)),
        "correlations": corr,
        "note": "Pearson correlations on daily values; they describe association, not causation.",
        "items": _records(
            merged[
                [
                    "date",
                    "generation_kwh",
                    "gti_wh",
                    "ghi_mj",
                    "sunshine_s",
                    "cloud_cover",
                    "temp_max",
                    "humidity",
                    "precip",
                    "weather_code",
                ]
            ]
        ),
    }


def compare_periods(db: Session, plant: Plant, *, include_synthetic: bool = False) -> dict[str, Any]:
    today = plant_today(plant)
    df = daily_frame(db, plant, include_synthetic=include_synthetic)
    if df.empty:
        return {"plant_id": plant.id, "periods": []}

    def period(name: str, a: date, b: date) -> dict[str, Any]:
        sel = df[(df["date"] >= a) & (df["date"] <= b)]
        return {
            "name": name,
            "start": a.isoformat(),
            "end": b.isoformat(),
            "days_with_data": int(len(sel)),
            "total_kwh": round(float(sel["generation_kwh"].sum()), 2) if not sel.empty else None,
            "avg_daily_kwh": round(float(sel["generation_kwh"].mean()), 2) if not sel.empty else None,
        }

    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    periods = [
        period("last_7_days", today - timedelta(days=6), today),
        period("previous_7_days", today - timedelta(days=13), today - timedelta(days=7)),
        period("this_month_to_date", month_start, today),
        period(
            "last_month_same_days",
            last_month_start,
            min(last_month_end, last_month_start + timedelta(days=today.day - 1)),
        ),
        period("last_month_full", last_month_start, last_month_end),
        period("last_30_days", today - timedelta(days=29), today),
        period("previous_30_days", today - timedelta(days=59), today - timedelta(days=30)),
    ]
    return {"plant_id": plant.id, "today": today.isoformat(), "periods": periods}


def intraday(db: Session, plant: Plant, day: date) -> dict[str, Any]:
    tz = ZoneInfo(plant.timezone or "UTC")
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    end = start + timedelta(days=1)
    rows = db.execute(
        select(
            EnergyReading.ts,
            EnergyReading.power_w,
            EnergyReading.energy_kwh,
            EnergyReading.temperature_c,
            EnergyReading.source,
            EnergyReading.quality_flags,
        )
        .where(
            EnergyReading.plant_id == plant.id,
            EnergyReading.ts >= start,
            EnergyReading.ts < end,
            EnergyReading.source != DataSource.SYNTHETIC,
        )
        .order_by(EnergyReading.ts)
    ).all()
    wx = db.execute(
        select(
            WeatherHourly.ts,
            WeatherHourly.shortwave_radiation,
            WeatherHourly.global_tilted_irradiance,
            WeatherHourly.cloud_cover,
            WeatherHourly.temperature_2m,
        )
        .where(
            WeatherHourly.plant_id == plant.id,
            WeatherHourly.kind == WeatherKind.OBSERVED,
            WeatherHourly.ts >= start,
            WeatherHourly.ts < end,
        )
        .order_by(WeatherHourly.ts)
    ).all()
    return {
        "plant_id": plant.id,
        "day": day.isoformat(),
        "readings": [
            {
                "ts": ts.isoformat(),
                "local_time": ts.astimezone(tz).strftime("%H:%M"),
                "power_w": p,
                "energy_kwh": e,
                "temperature_c": t,
                "source": s.value,
                "flags": f,
            }
            for ts, p, e, t, s, f in rows
        ],
        "weather_hourly": [
            {
                "ts": ts.isoformat(),
                "local_time": ts.astimezone(tz).strftime("%H:%M"),
                "ghi": g,
                "gti": gt,
                "cloud_cover": c,
                "temperature": t,
            }
            for ts, g, gt, c, t in wx
        ],
        "sources": sorted({s.value for *_, s, _ in rows}),
    }


# --------------------------------------------------------------------------- savings


def savings(
    db: Session,
    plant: Plant,
    start: date | None = None,
    end: date | None = None,
    *,
    include_synthetic: bool = False,
) -> dict[str, Any]:
    df = daily_frame(db, plant, start, end, include_synthetic=include_synthetic)
    missing: list[str] = []
    if plant.tariff_import_per_kwh is None:
        missing.append("tariff_import_per_kwh (your electricity tariff) is not set in Settings")
    gen = float(df["generation_kwh"].sum()) if not df.empty else 0.0
    measured_self = (
        df["self_consumed_kwh"].notna().any() if "self_consumed_kwh" in df and not df.empty else False
    )
    measured_export = df["exported_kwh"].notna().any() if not df.empty else False
    out: dict[str, Any] = {
        "plant_id": plant.id,
        "currency": plant.currency,
        "period": {
            "start": str(df["date"].min()) if not df.empty else None,
            "end": str(df["date"].max()) if not df.empty else None,
            "days": int(len(df)),
        },
        "generation_kwh": round(gen, 2),
        "tariff_import_per_kwh": plant.tariff_import_per_kwh,
        "tariff_export_per_kwh": plant.tariff_export_per_kwh,
        "self_consumption_measured": bool(measured_self),
        "export_measured": bool(measured_export),
        "is_estimate": True,
        "missing_inputs": missing,
        "notes": [],
    }
    if not measured_self and not measured_export:
        out["notes"].append(
            "No smart meter / grid data: self-consumption and export are not measured. "
            "Savings are shown as a range between 'all exported' and 'all self-consumed'."
        )
    if plant.tariff_import_per_kwh is not None:
        imp = plant.tariff_import_per_kwh
        exp = plant.tariff_export_per_kwh if plant.tariff_export_per_kwh is not None else imp
        if measured_self or measured_export:
            self_kwh = float(df["self_consumed_kwh"].fillna(0).sum()) if "self_consumed_kwh" in df else 0.0
            export_kwh = float(df["exported_kwh"].fillna(0).sum())
            value = self_kwh * imp + export_kwh * exp
            out["estimated_value"] = round(value, 2)
            out["self_consumed_kwh"] = round(self_kwh, 2)
            out["exported_kwh"] = round(export_kwh, 2)
        else:
            out["estimated_value_if_all_self_consumed"] = round(gen * imp, 2)
            out["estimated_value_if_all_exported"] = round(gen * exp, 2)
            out["estimated_value"] = round(gen * imp, 2)
            out["notes"].append(
                "Headline estimate assumes all generation offsets grid purchases at the import tariff."
            )
        if plant.installation_cost and out.get("estimated_value") and len(df) >= 7:
            per_day = out["estimated_value"] / max(len(df), 1)
            years = plant.installation_cost / (per_day * 365) if per_day > 0 else None
            out["roi"] = {
                "installation_cost": plant.installation_cost,
                "value_to_date": out["estimated_value"],
                "recovered_fraction": round(out["estimated_value"] / plant.installation_cost, 4),
                "simple_payback_years_estimate": round(years, 1) if years else None,
                "note": "Simple payback extrapolates the average daily value over the data period; seasonality is not modelled.",
            }
    return out


def monthly_savings(db: Session, plant: Plant, *, include_synthetic: bool = False) -> dict[str, Any]:
    df = daily_frame(db, plant, include_synthetic=include_synthetic)
    if df.empty or plant.tariff_import_per_kwh is None:
        return {
            "plant_id": plant.id,
            "items": [],
            "missing_inputs": ["tariff_import_per_kwh"] if plant.tariff_import_per_kwh is None else [],
        }
    df["month"] = pd.to_datetime(df["date"]).dt.to_period("M").astype(str)
    g = df.groupby("month")["generation_kwh"].sum().reset_index()
    imp = plant.tariff_import_per_kwh
    exp = plant.tariff_export_per_kwh if plant.tariff_export_per_kwh is not None else imp
    g["value_if_self_consumed"] = (g["generation_kwh"] * imp).round(2)
    g["value_if_exported"] = (g["generation_kwh"] * exp).round(2)
    g["generation_kwh"] = g["generation_kwh"].round(2)
    return {"plant_id": plant.id, "currency": plant.currency, "is_estimate": True, "items": _records(g)}


def and_filters(*conds):  # small helper kept for route composition
    return and_(*conds)
