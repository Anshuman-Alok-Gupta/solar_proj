"""Performance reports (daily / weekly / monthly) assembled from stored data only."""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db.models import Anomaly, Forecast, ModelVersion, Plant, Report, ReportKind
from app.services import analytics as an


def _period(kind: ReportKind, anchor: date) -> tuple[date, date]:
    if kind == ReportKind.DAILY:
        return anchor, anchor
    if kind == ReportKind.WEEKLY:
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=6)
    start = anchor.replace(day=1)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt - timedelta(days=1)


def build_report(
    db: Session,
    plant: Plant,
    kind: ReportKind,
    anchor: date | None = None,
    *,
    include_synthetic: bool = False,
) -> dict[str, Any]:
    today = an.plant_today(plant)
    anchor = anchor or (today - timedelta(days=1) if kind == ReportKind.DAILY else today)
    start, end = _period(kind, anchor)
    end_eff = min(end, today)
    series = an.daily_series(db, plant, start, end_eff, include_synthetic=include_synthetic)
    items = series["items"]
    complete = [i for i in items if i.get("is_complete")]
    prev_start, prev_end = _period(kind, start - timedelta(days=1))
    # Pro-rate: compare the same number of elapsed days when the current period is partial
    prev_end = min(prev_end, prev_start + (end_eff - start))
    prev = an.daily_series(db, plant, prev_start, prev_end, include_synthetic=include_synthetic)
    gen = [i["generation_kwh"] for i in complete]
    best = max(complete, key=lambda i: i["generation_kwh"]) if complete else None
    worst = min(complete, key=lambda i: i["generation_kwh"]) if complete else None
    wx = [i for i in items if i.get("gti_wh") is not None]
    weather = {
        "days_with_weather": len(wx),
        "mean_gti_wh_m2": round(sum(i["gti_wh"] for i in wx) / len(wx), 1) if wx else None,
        "mean_cloud_cover_pct": round(
            sum(i["cloud_cover"] for i in wx if i.get("cloud_cover") is not None)
            / max(1, sum(1 for i in wx if i.get("cloud_cover") is not None)),
            1,
        )
        if wx
        else None,
        "mean_temp_max_c": round(
            sum(i["temp_max"] for i in wx if i.get("temp_max") is not None)
            / max(1, sum(1 for i in wx if i.get("temp_max") is not None)),
            1,
        )
        if wx
        else None,
        "sunniest_day": max(wx, key=lambda i: i["gti_wh"])["date"] if wx else None,
        "cloudiest_day": min(wx, key=lambda i: i["gti_wh"])["date"] if wx else None,
    }
    anomalies = db.scalars(
        select(Anomaly)
        .where(Anomaly.plant_id == plant.id, Anomaly.date >= start, Anomaly.date <= end_eff)
        .order_by(Anomaly.date)
    ).all()
    forecasts = db.scalars(
        select(Forecast).where(
            Forecast.plant_id == plant.id,
            Forecast.target_date >= start,
            Forecast.target_date <= end_eff,
            Forecast.actual_kwh.is_not(None),
        )
    ).all()
    fc_scored = [(f.predicted_kwh, f.actual_kwh) for f in forecasts]
    model = db.scalar(
        select(ModelVersion).where(ModelVersion.plant_id == plant.id, ModelVersion.is_active.is_(True))
    )
    next_fc = db.scalar(
        select(Forecast)
        .where(Forecast.plant_id == plant.id, Forecast.target_date > end_eff)
        .order_by(Forecast.target_date, desc(Forecast.issued_at))
        .limit(1)
    )
    savings = an.savings(db, plant, start, end_eff, include_synthetic=include_synthetic)
    total = round(sum(gen), 2) if gen else 0.0
    prev_total = prev["total_kwh"] if prev["items"] else None
    content = {
        "kind": kind.value,
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "effective_end": end_eff.isoformat(),
            "days_with_data": len(items),
            "complete_days": len(complete),
        },
        "generation": {
            "total_kwh": total,
            "avg_daily_kwh": round(total / len(gen), 2) if gen else None,
            "specific_yield_kwh_per_kw": round(total / plant.capacity_kw, 2)
            if plant.capacity_kw and gen
            else None,
            "best_day": {"date": best["date"], "kwh": best["generation_kwh"]} if best else None,
            "lowest_day": {"date": worst["date"], "kwh": worst["generation_kwh"]} if worst else None,
            "previous_period_total_kwh": prev_total,
            "previous_period": {
                "start": prev_start.isoformat(),
                "end": prev_end.isoformat(),
                "days_with_data": len(prev["items"]),
            },
            "change_vs_previous_pct": round((total - prev_total) / prev_total * 100, 1)
            if prev_total
            else None,
            "includes_partial_today": any(not i.get("is_complete") for i in items),
        },
        "weather": weather,
        "anomalies": [
            {
                "date": a.date.isoformat(),
                "category": a.category,
                "severity": a.severity.value,
                "status": a.status.value,
                "title": a.title,
                "actual_kwh": a.actual_kwh,
                "expected_kwh": a.expected_kwh,
                "deviation_pct": a.deviation_pct,
            }
            for a in anomalies
        ],
        "forecast_performance": {
            "scored_days": len(fc_scored),
            "mae_kwh": round(sum(abs(p - a) for p, a in fc_scored) / len(fc_scored), 2)
            if fc_scored
            else None,
            "note": "Compares forecasts issued with forecast weather against actual generation",
        },
        "next_forecast": {
            "target_date": next_fc.target_date.isoformat(),
            "predicted_kwh": next_fc.predicted_kwh,
            "lower_kwh": next_fc.lower_kwh,
            "upper_kwh": next_fc.upper_kwh,
        }
        if next_fc
        else None,
        "model": {
            "algorithm": model.algorithm,
            "model_version_id": model.id,
            "validation_mae": (model.metrics or {}).get("validation", {}).get("mae"),
            "test_mae": (model.metrics or {}).get("test", {}).get("mae"),
        }
        if model
        else None,
        "savings": {
            k: savings.get(k)
            for k in (
                "currency",
                "estimated_value",
                "estimated_value_if_all_self_consumed",
                "estimated_value_if_all_exported",
                "is_estimate",
                "missing_inputs",
                "notes",
            )
        },
        "daily": [
            {
                "date": i["date"],
                "generation_kwh": i["generation_kwh"],
                "gti_wh": i.get("gti_wh"),
                "cloud_cover": i.get("cloud_cover"),
                "is_complete": i.get("is_complete"),
            }
            for i in items
        ],
        "data_sources": sorted({i["source"] for i in items}),
    }
    title = f"{kind.value.capitalize()} performance report {start.isoformat()}" + (
        "" if kind == ReportKind.DAILY else f" to {end.isoformat()}"
    )
    summary = _summary_text(plant, content)
    return {
        "title": title,
        "period_start": start,
        "period_end": end,
        "content": content,
        "summary_text": summary,
    }


def _summary_text(plant: Plant, c: dict[str, Any]) -> str:
    g = c["generation"]
    parts = [
        f"{plant.name}: {g['total_kwh']} kWh over {c['period']['complete_days']} complete day(s) ({c['period']['start']} to {c['period']['effective_end']})."
    ]
    if g["avg_daily_kwh"] is not None:
        parts.append(
            f"Average {g['avg_daily_kwh']} kWh/day"
            + (
                f", {g['specific_yield_kwh_per_kw']} kWh per kW installed."
                if g["specific_yield_kwh_per_kw"] is not None
                else "."
            )
        )
    if g["best_day"]:
        parts.append(
            f"Best day {g['best_day']['date']} ({g['best_day']['kwh']} kWh); lowest {g['lowest_day']['date']} ({g['lowest_day']['kwh']} kWh)."
        )
    if g["change_vs_previous_pct"] is not None:
        parts.append(
            f"{'Up' if g['change_vs_previous_pct'] >= 0 else 'Down'} {abs(g['change_vs_previous_pct'])}% versus the previous period ({g['previous_period_total_kwh']} kWh)."
        )
    if c["weather"]["mean_gti_wh_m2"] is not None:
        parts.append(
            f"Weather: mean tilted irradiance {c['weather']['mean_gti_wh_m2']} Wh/m², mean cloud cover {c['weather']['mean_cloud_cover_pct']}%."
        )
    n = len(c["anomalies"])
    parts.append(f"{n} anomaly record(s) in the period." if n else "No anomalies recorded in the period.")
    if c["savings"].get("estimated_value") is not None:
        parts.append(
            f"Estimated value {c['savings']['currency']} {c['savings']['estimated_value']} (estimate; see notes)."
        )
    elif c["savings"].get("missing_inputs"):
        parts.append("Savings not estimated: " + "; ".join(c["savings"]["missing_inputs"]) + ".")
    return " ".join(parts)


def save_report(db: Session, plant: Plant, built: dict[str, Any], kind: ReportKind) -> Report:
    rep = Report(
        plant_id=plant.id,
        kind=kind,
        period_start=built["period_start"],
        period_end=built["period_end"],
        title=built["title"],
        content=built["content"],
        summary_text=built["summary_text"],
    )
    db.add(rep)
    db.commit()
    db.refresh(rep)
    return rep


def report_csv(rep: Report) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "generation_kwh", "gti_wh_m2", "cloud_cover_pct", "is_complete"])
    for d in rep.content.get("daily", []):
        w.writerow(
            [d["date"], d["generation_kwh"], d.get("gti_wh"), d.get("cloud_cover"), d.get("is_complete")]
        )
    w.writerow([])
    w.writerow(["summary", rep.summary_text])
    return buf.getvalue()
