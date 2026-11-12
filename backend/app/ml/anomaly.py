"""Anomaly detection on daily generation (weather-adjusted) plus intraday fluctuation checks.

Detectors
---------
* residual   : actual vs model-expected (observed weather) -> drops / underperformance
* coverage   : missing days and zero-generation days with meaningful irradiance
* quality    : rows carrying data-quality flags
* intraday   : unusual power fluctuations in 5-minute readings (only when readings exist)
* isolation  : IsolationForest over (generation, irradiance, cloud, temperature, residual)

Every anomaly records actual/expected/deviation, evidence and recommended checks and
explicitly avoids claiming hardware failure: weather, maintenance and data gaps are listed
as alternative explanations.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import (
    Anomaly,
    AnomalySeverity,
    AnomalyStatus,
    DailyEnergy,
    DataSource,
    EnergyReading,
    Plant,
)
from app.ml.inference import NoActiveModel, expected_for_dates, explain_prediction, observed_weather
from app.ml.registry import get_active, load_artifact
from app.services.analytics import plant_today

log = logging.getLogger(__name__)

CHECKS = {
    "unexpected_production_drop": [
        "Compare with the weather summary: heavy cloud, rain or dust storms explain most single-day drops.",
        "Check the SEMS app for inverter alarms or an offline period on that day.",
        "Look at the intraday power curve for a flat section (outage) versus a uniformly low curve (soiling/shading).",
        "If the drop repeats on clear days, inspect panels for soiling, bird droppings or new shading.",
    ],
    "possible_underperformance": [
        "Several consecutive days below expectation: check for persistent shading, soiling or a tripped string.",
        "Compare the recent performance ratio with the first weeks after installation.",
        "Verify the inverter is not derating (high chamber temperature) during midday.",
        "Ask the installer to check string voltages/currents if the pattern persists for a week.",
    ],
    "missing_readings": [
        "Check whether the inverter/Wi-Fi dongle was offline; data may still arrive after reconnection.",
        "Re-run the SEMS sync or import the day's operation report from the SEMS app.",
        "A missing day is a data gap, not evidence of a hardware problem.",
    ],
    "zero_generation": [
        "Confirm in the SEMS app whether the inverter reported 0 kWh or simply did not report.",
        "Check for a grid outage, maintenance or an AC breaker trip on that day.",
        "If irradiance was high and the inverter was online, contact the installer.",
    ],
    "unusual_power_fluctuations": [
        "Rapid power swings on a clear day can indicate intermittent shading, grid voltage trips or a loose DC connector.",
        "Compare the fluctuations with cloud cover for the same hours before suspecting equipment.",
        "Check inverter event logs for repeated grid-protection trips.",
    ],
    "data_quality_issue": [
        "The day's data carries quality flags; verify the source before interpreting the value.",
        "Re-import or re-sync the day if the flags indicate a partial or inconsistent record.",
    ],
    "statistical_outlier": [
        "The day is unusual relative to weather and recent history; review it together with the other evidence.",
    ],
}


def _severity_from_pct(pct: float) -> AnomalySeverity:
    if pct <= -50:
        return AnomalySeverity.HIGH
    if pct <= -30:
        return AnomalySeverity.MEDIUM
    return AnomalySeverity.LOW


def _upsert(db: Session, rows: list[dict[str, Any]]) -> int:
    n = 0
    for r in rows:
        stmt = pg_insert(Anomaly).values(**r)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_anomalies_plant_id_date_category_detector",
            set_={
                "severity": stmt.excluded.severity,
                "actual_kwh": stmt.excluded.actual_kwh,
                "expected_kwh": stmt.excluded.expected_kwh,
                "deviation_kwh": stmt.excluded.deviation_kwh,
                "deviation_pct": stmt.excluded.deviation_pct,
                "score": stmt.excluded.score,
                "title": stmt.excluded.title,
                "description": stmt.excluded.description,
                "evidence": stmt.excluded.evidence,
                "recommended_checks": stmt.excluded.recommended_checks,
                "model_version_id": stmt.excluded.model_version_id,
                "updated_at": datetime.now(UTC),
            },
        )
        db.execute(stmt)
        n += 1
    return n


def _weather_summary(w: pd.Series | None) -> dict[str, Any]:
    if w is None:
        return {}
    return {
        k: (None if pd.isna(w.get(k)) else round(float(w.get(k)), 2))
        for k in ("gti_wh", "ghi_mj", "sunshine_h", "cloud_cover", "temp_max", "precip")
        if k in w
    }


def detect_intraday_fluctuations(db: Session, plant: Plant, day: date) -> dict[str, Any] | None:
    tz = ZoneInfo(plant.timezone or "UTC")
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    rows = db.execute(
        select(EnergyReading.ts, EnergyReading.power_w)
        .where(
            EnergyReading.plant_id == plant.id,
            EnergyReading.ts >= start,
            EnergyReading.ts < start + timedelta(days=1),
            EnergyReading.source != DataSource.SYNTHETIC,
            EnergyReading.power_w.is_not(None),
        )
        .order_by(EnergyReading.ts)
    ).all()
    if len(rows) < 50:
        return None
    df = pd.DataFrame(rows, columns=["ts", "power_w"])
    cap = (plant.capacity_kw or 5.0) * 1000
    step = df["ts"].diff().dt.total_seconds().median() / 60
    if step > 20:
        return None
    daylight = df[df["power_w"] > cap * 0.05]
    if len(daylight) < 20:
        return None
    delta = daylight["power_w"].diff().abs()
    roll_max = daylight["power_w"].rolling(6, min_periods=3).max()
    events = int(((delta > cap * 0.3) & (roll_max > cap * 0.3)).sum())
    ratio = events / len(daylight)
    return {
        "readings": int(len(df)),
        "daylight_readings": int(len(daylight)),
        "step_minutes": round(step, 1),
        "large_swings": events,
        "swing_ratio": round(ratio, 3),
        "peak_power_w": float(daylight["power_w"].max()),
    }


def detect_anomalies(
    db: Session, plant: Plant, *, lookback_days: int = 60, include_synthetic: bool = False
) -> dict[str, Any]:
    today = plant_today(plant)
    start = today - timedelta(days=lookback_days)
    sources = [DataSource.SEMS_API, DataSource.CSV_IMPORT] + (
        [DataSource.SYNTHETIC] if include_synthetic else []
    )
    rows = db.execute(
        select(
            DailyEnergy.date,
            DailyEnergy.generation_kwh,
            DailyEnergy.is_complete,
            DailyEnergy.quality_flags,
            DailyEnergy.source,
        )
        .where(DailyEnergy.plant_id == plant.id, DailyEnergy.date >= start, DailyEnergy.source.in_(sources))
        .order_by(DailyEnergy.date)
    ).all()
    daily = pd.DataFrame(rows, columns=["date", "generation_kwh", "is_complete", "quality_flags", "source"])
    if daily.empty:
        return {"created_or_updated": 0, "notes": ["No daily data in the lookback window"]}
    complete = daily[daily["is_complete"]].copy()
    weather = observed_weather(db, plant).set_index("date")
    out_rows: list[dict[str, Any]] = []
    notes: list[str] = []

    # ---- model-based residuals
    mv = get_active(db, plant.id)
    expected: dict[date, float] = {}
    sigma = None
    X = None
    art = None
    if mv is not None and not complete.empty:
        try:
            res = expected_for_dates(db, plant, complete["date"].tolist(), model=mv)
            expected = res["expected"]
            sigma = res["residual_std"]
            X = res["features"]
            art = load_artifact(mv.artifact_path)
        except NoActiveModel:
            pass
        except Exception as exc:  # pragma: no cover
            log.exception("Expected-generation computation failed")
            notes.append(f"Model expectation unavailable: {exc}")
    else:
        notes.append("No active model: residual-based detection skipped (train a model first)")

    complete["expected"] = complete["date"].map(expected)
    complete["residual"] = complete["generation_kwh"] - complete["expected"]
    if sigma is None and complete["residual"].notna().sum() > 5:
        sigma = float(complete["residual"].std())
    complete["z"] = complete["residual"] / sigma if sigma else np.nan
    gti_ref = (
        float(weather["gti_wh"].dropna().quantile(0.9))
        if "gti_wh" in weather and weather["gti_wh"].notna().any()
        else None
    )

    streak = 0
    for _, r in complete.iterrows():
        d = r["date"]
        w = weather.loc[d] if d in weather.index else None
        wsum = _weather_summary(w)
        gti = wsum.get("gti_wh")
        exp = r["expected"]
        z = r["z"]
        actual = float(r["generation_kwh"])
        pct = (actual - exp) / exp * 100 if exp and exp > 0 else None
        neighbours = complete[
            (complete["date"] >= d - timedelta(days=3))
            & (complete["date"] <= d + timedelta(days=3))
            & (complete["date"] != d)
        ]
        base_evidence = {
            "weather": wsum,
            "neighbouring_days_kwh": {
                str(x["date"]): round(float(x["generation_kwh"]), 2) for _, x in neighbours.iterrows()
            },
            "model": {
                "model_version_id": mv.id if mv else None,
                "algorithm": art["algorithm"] if art else None,
                "residual_sigma_kwh": round(sigma, 3) if sigma else None,
            },
            "z_score": round(float(z), 2) if pd.notna(z) else None,
            "source": r["source"].value,
        }
        # zero generation with meaningful irradiance
        if actual <= 0.05 and gti and gti > 1500:
            out_rows.append(
                {
                    "plant_id": plant.id,
                    "model_version_id": mv.id if mv else None,
                    "date": d,
                    "category": "zero_generation",
                    "detector": "coverage",
                    "severity": AnomalySeverity.HIGH,
                    "actual_kwh": actual,
                    "expected_kwh": exp,
                    "deviation_kwh": (actual - exp) if exp else None,
                    "deviation_pct": pct,
                    "score": 1.0,
                    "title": f"No generation recorded on {d.isoformat()} despite usable sunlight",
                    "description": "The day shows ~0 kWh while irradiance was substantial. This may be an outage, a communication gap or a recording error rather than equipment failure.",
                    "evidence": base_evidence,
                    "recommended_checks": CHECKS["zero_generation"],
                }
            )
            streak = 0
            continue
        if pd.notna(z) and exp:
            good_weather = gti is not None and gti_ref is not None and gti >= 0.6 * gti_ref
            if z <= -2.0 and pct is not None and pct <= -25:
                expl = (
                    explain_prediction(art, X.loc[[d]])
                    if art is not None and X is not None and d in X.index
                    else None
                )
                out_rows.append(
                    {
                        "plant_id": plant.id,
                        "model_version_id": mv.id,
                        "date": d,
                        "category": "unexpected_production_drop",
                        "detector": "residual",
                        "severity": _severity_from_pct(pct) if good_weather else AnomalySeverity.LOW,
                        "actual_kwh": actual,
                        "expected_kwh": round(exp, 2),
                        "deviation_kwh": round(actual - exp, 2),
                        "deviation_pct": round(pct, 1),
                        "score": round(float(-z), 2),
                        "title": f"Generation {abs(pct):.0f}% below weather-adjusted expectation on {d.isoformat()}",
                        "description": (
                            f"Actual {actual:.1f} kWh vs expected {exp:.1f} kWh given the day's weather. "
                            + (
                                "Irradiance was high, so weather alone does not explain the gap."
                                if good_weather
                                else "Irradiance was also low, so weather is a plausible explanation; severity reduced."
                            )
                        ),
                        "evidence": {**base_evidence, "explanation": expl},
                        "recommended_checks": CHECKS["unexpected_production_drop"],
                    }
                )
            streak = streak + 1 if z <= -1.0 else 0
            if streak >= 3:
                window = complete[(complete["date"] > d - timedelta(days=streak)) & (complete["date"] <= d)]
                mean_pct = float(
                    ((window["generation_kwh"] - window["expected"]) / window["expected"]).mean() * 100
                )
                out_rows.append(
                    {
                        "plant_id": plant.id,
                        "model_version_id": mv.id,
                        "date": d,
                        "category": "possible_underperformance",
                        "detector": "residual",
                        "severity": AnomalySeverity.MEDIUM if mean_pct <= -20 else AnomalySeverity.LOW,
                        "actual_kwh": actual,
                        "expected_kwh": round(exp, 2),
                        "deviation_kwh": round(actual - exp, 2),
                        "deviation_pct": round(pct, 1) if pct is not None else None,
                        "score": round(float(-z), 2),
                        "title": f"{streak} consecutive days below expectation (ending {d.isoformat()})",
                        "description": f"Average shortfall of {abs(mean_pct):.0f}% over {streak} days after adjusting for weather. Persistent patterns deserve a closer look but are not proof of a fault.",
                        "evidence": {
                            **base_evidence,
                            "streak_days": streak,
                            "mean_shortfall_pct": round(mean_pct, 1),
                        },
                        "recommended_checks": CHECKS["possible_underperformance"],
                    }
                )
        else:
            streak = 0
        flags = r["quality_flags"] or []
        bad = [f for f in flags if f not in ("no_smart_meter_consumption_unmeasured",)]
        if bad:
            out_rows.append(
                {
                    "plant_id": plant.id,
                    "model_version_id": None,
                    "date": d,
                    "category": "data_quality_issue",
                    "detector": "quality",
                    "severity": AnomalySeverity.INFO,
                    "actual_kwh": actual,
                    "expected_kwh": exp,
                    "deviation_kwh": None,
                    "deviation_pct": None,
                    "score": None,
                    "title": f"Data quality flags on {d.isoformat()}: {', '.join(bad)}",
                    "description": "The stored value carries quality flags from ingestion.",
                    "evidence": {**base_evidence, "flags": bad},
                    "recommended_checks": CHECKS["data_quality_issue"],
                }
            )

    # ---- missing days
    all_days = pd.date_range(max(start, daily["date"].min()), today - timedelta(days=1), freq="D").date
    have = set(daily["date"])
    for d in all_days:
        if d not in have and d in weather.index:
            out_rows.append(
                {
                    "plant_id": plant.id,
                    "model_version_id": None,
                    "date": d,
                    "category": "missing_readings",
                    "detector": "coverage",
                    "severity": AnomalySeverity.MEDIUM,
                    "actual_kwh": None,
                    "expected_kwh": round(float(expected.get(d, np.nan)), 2)
                    if d in expected and pd.notna(expected.get(d))
                    else None,
                    "deviation_kwh": None,
                    "deviation_pct": None,
                    "score": None,
                    "title": f"No daily record for {d.isoformat()}",
                    "description": "No generation value exists for this day in any data source.",
                    "evidence": {"weather": _weather_summary(weather.loc[d])},
                    "recommended_checks": CHECKS["missing_readings"],
                }
            )

    # ---- intraday fluctuations
    for d in complete["date"].tolist()[-lookback_days:]:
        stats = detect_intraday_fluctuations(db, plant, d)
        if stats and stats["swing_ratio"] > 0.08 and stats["large_swings"] >= 5:
            w = weather.loc[d] if d in weather.index else None
            out_rows.append(
                {
                    "plant_id": plant.id,
                    "model_version_id": None,
                    "date": d,
                    "category": "unusual_power_fluctuations",
                    "detector": "intraday",
                    "severity": AnomalySeverity.LOW
                    if (w is not None and pd.notna(w.get("cloud_cover")) and w.get("cloud_cover") > 40)
                    else AnomalySeverity.MEDIUM,
                    "actual_kwh": float(complete.loc[complete["date"] == d, "generation_kwh"].iloc[0]),
                    "expected_kwh": expected.get(d),
                    "deviation_kwh": None,
                    "deviation_pct": None,
                    "score": stats["swing_ratio"],
                    "title": f"{stats['large_swings']} large power swings on {d.isoformat()}",
                    "description": "Consecutive 5-minute readings changed by more than 30% of capacity unusually often.",
                    "evidence": {"intraday": stats, "weather": _weather_summary(w)},
                    "recommended_checks": CHECKS["unusual_power_fluctuations"],
                }
            )

    # ---- isolation forest (secondary, low severity)
    feats = complete.merge(weather.reset_index(), on="date", how="left")[
        ["date", "generation_kwh", "gti_wh", "cloud_cover", "temp_max", "residual"]
    ].dropna(subset=["generation_kwh", "gti_wh"])
    flagged_dates = {r["date"] for r in out_rows}
    if len(feats) >= 20:
        from sklearn.ensemble import IsolationForest

        mat = feats[["generation_kwh", "gti_wh", "cloud_cover", "temp_max", "residual"]].fillna(
            feats[["generation_kwh", "gti_wh", "cloud_cover", "temp_max", "residual"]].median()
        )
        iso = IsolationForest(n_estimators=200, contamination=0.05, random_state=42).fit(mat)
        scores = -iso.score_samples(mat)
        preds = iso.predict(mat)
        for (_, r), s, p in zip(feats.iterrows(), scores, preds, strict=True):
            if p == -1 and r["date"] not in flagged_dates:
                out_rows.append(
                    {
                        "plant_id": plant.id,
                        "model_version_id": mv.id if mv else None,
                        "date": r["date"],
                        "category": "statistical_outlier",
                        "detector": "isolation_forest",
                        "severity": AnomalySeverity.LOW,
                        "actual_kwh": float(r["generation_kwh"]),
                        "expected_kwh": round(float(expected.get(r["date"], np.nan)), 2)
                        if pd.notna(expected.get(r["date"], np.nan))
                        else None,
                        "deviation_kwh": round(float(r["residual"]), 2) if pd.notna(r["residual"]) else None,
                        "deviation_pct": None,
                        "score": round(float(s), 3),
                        "title": f"Unusual day relative to weather and history: {r['date'].isoformat()}",
                        "description": "Isolation Forest flagged this day as an outlier in (generation, irradiance, cloud, temperature, residual) space.",
                        "evidence": {
                            "weather": _weather_summary(
                                weather.loc[r["date"]] if r["date"] in weather.index else None
                            ),
                            "isolation_score": round(float(s), 3),
                        },
                        "recommended_checks": CHECKS["statistical_outlier"],
                    }
                )

    n = _upsert(db, out_rows)
    db.commit()
    by_cat: dict[str, int] = {}
    for r in out_rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    return {
        "created_or_updated": n,
        "by_category": by_cat,
        "window": {"start": start.isoformat(), "end": today.isoformat()},
        "model_version_id": mv.id if mv else None,
        "residual_sigma_kwh": round(sigma, 3) if sigma else None,
        "notes": notes,
    }


def set_status(db: Session, anomaly: Anomaly, status: AnomalyStatus) -> Anomaly:
    anomaly.status = status
    db.commit()
    db.refresh(anomaly)
    return anomaly
