"""Model Lab, forecasting and anomaly endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.api.deps import DB, PlantDep
from app.db.models import Anomaly, AnomalySeverity, AnomalyStatus, ModelVersion, SyncLog
from app.ml import anomaly as anomaly_svc
from app.ml.explain import global_importance
from app.ml.inference import (
    NoActiveModel,
    backfill_actuals,
    expected_for_dates,
    forecast_history,
    issue_forecasts,
)
from app.ml.registry import get_active, load_artifact, set_active
from app.ml.train import ALGORITHMS, to_jsonable, train_models

router = APIRouter(prefix="/plants/{plant_id}", tags=["ml"])


class TrainRequest(BaseModel):
    algorithms: list[str] | None = None
    include_synthetic: bool = False
    activate_best: bool = True


def _mv_out(mv: ModelVersion) -> dict[str, Any]:
    return {
        "id": mv.id,
        "algorithm": mv.algorithm,
        "version_tag": mv.version_tag,
        "trained_at": mv.trained_at.isoformat(),
        "is_active": mv.is_active,
        "is_baseline": mv.is_baseline,
        "metrics": mv.metrics,
        "feature_names": mv.feature_names,
        "feature_importance": mv.feature_importance,
        "hyperparameters": mv.hyperparameters,
        "training_start": mv.training_start.isoformat() if mv.training_start else None,
        "training_end": mv.training_end.isoformat() if mv.training_end else None,
        "test_start": mv.test_start.isoformat() if mv.test_start else None,
        "test_end": mv.test_end.isoformat() if mv.test_end else None,
        "row_counts": mv.row_counts,
        "data_sources": mv.data_sources,
        "dataset_fingerprint": mv.dataset_fingerprint,
        "notes": mv.notes,
    }


# --------------------------------------------------------------------------- model lab


@router.post("/ml/train")
def train(plant: PlantDep, db: DB, payload: TrainRequest | None = None) -> dict:
    payload = payload or TrainRequest()
    bad = [a for a in (payload.algorithms or []) if a not in ALGORITHMS]
    if bad:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Unknown algorithms {bad}; choose from {ALGORITHMS}"
        )
    try:
        return to_jsonable(
            train_models(
                db,
                plant,
                algorithms=payload.algorithms,
                include_synthetic=payload.include_synthetic,
                activate_best=payload.activate_best,
            )
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/ml/nightly-run")
def nightly_run(plant: PlantDep, db: DB, force_train: bool = False) -> dict:
    """Run the nightly upkeep now (retrain if data changed, forecasts, anomaly detection)."""
    from app.ml.nightly import run_nightly

    row = run_nightly(db, plant, force_train=force_train)
    return _nightly_out(row)


@router.get("/ml/nightly-status")
def nightly_status(plant: PlantDep, db: DB) -> dict:
    from app.core.config import get_settings
    from app.scheduler import next_run

    s = get_settings()
    last = db.scalar(
        select(SyncLog)
        .where(SyncLog.plant_id == plant.id, SyncLog.source == "ml")
        .order_by(desc(SyncLog.started_at))
        .limit(1)
    )
    nxt = next_run("ml_nightly")
    return {
        "enabled": s.ml_nightly_enabled,
        "schedule": f"daily at {s.ml_nightly_hour:02d}:00 {s.ml_nightly_timezone}",
        "next_run": nxt.isoformat() if nxt else None,
        "last_run": _nightly_out(last) if last else None,
    }


def _nightly_out(row) -> dict:
    return {
        "status": row.status.value,
        "started_at": row.started_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "message": row.message,
        "error": row.error,
        "details": row.details,
    }


@router.get("/ml/models")
def list_models(plant: PlantDep, db: DB, limit: int = Query(50, le=500)) -> dict:
    rows = db.scalars(
        select(ModelVersion)
        .where(ModelVersion.plant_id == plant.id)
        .order_by(desc(ModelVersion.trained_at), ModelVersion.id)
        .limit(limit)
    ).all()
    active = get_active(db, plant.id)
    latest_tag = rows[0].version_tag.rsplit("_", 1)[0] if rows else None
    return {
        "plant_id": plant.id,
        "active_model_id": active.id if active else None,
        "latest_run_prefix": latest_tag,
        "algorithms": ALGORITHMS,
        "items": [_mv_out(m) for m in rows],
    }


@router.get("/ml/models/{model_id}")
def model_detail(model_id: int, plant: PlantDep, db: DB) -> dict:
    mv = db.get(ModelVersion, model_id)
    if mv is None or mv.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    out = _mv_out(mv)
    if mv.artifact_path:
        try:
            art = load_artifact(mv)
            out["validation_predictions"] = art.get("val_predictions", [])
            out["test_predictions"] = art.get("test_predictions", [])
            out["folds"] = art.get("folds", [])
            out["residual_quantiles"] = art.get("residual_quantiles")
            out["residual_std"] = art.get("residual_std")
        except FileNotFoundError:
            out["artifact_missing"] = True
    return out


@router.post("/ml/models/{model_id}/activate")
def activate(model_id: int, plant: PlantDep, db: DB) -> dict:
    mv = db.get(ModelVersion, model_id)
    if mv is None or mv.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Model not found")
    set_active(db, plant.id, mv.id)
    db.commit()
    return {"active_model_id": mv.id, "algorithm": mv.algorithm}


@router.get("/ml/explain")
def explain(plant: PlantDep, db: DB, model_id: int | None = None) -> dict:
    mv = db.get(ModelVersion, model_id) if model_id else get_active(db, plant.id)
    if mv is None or mv.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No active model (train one first)")
    if mv.is_baseline:
        return {
            "model_version_id": mv.id,
            "algorithm": mv.algorithm,
            "shap": None,
            "note": "Baselines have no SHAP explanation; feature importance not applicable",
        }
    art = load_artifact(mv)
    from app.ml.train import load_training_data

    frame, _ = load_training_data(db, plant)
    X = frame[art["features"]]
    return {
        "model_version_id": mv.id,
        "algorithm": mv.algorithm,
        "shap": global_importance(art, X),
        "native_importance": mv.feature_importance,
    }


# --------------------------------------------------------------------------- forecasts


@router.post("/forecasts/issue")
def issue(plant: PlantDep, db: DB, days: int = Query(3, ge=1, le=7)) -> dict:
    try:
        return to_jsonable(issue_forecasts(db, plant, days=days))
    except NoActiveModel as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.get("/forecasts")
def forecasts(plant: PlantDep, db: DB, days: int = Query(60, ge=1, le=400)) -> dict:
    backfill_actuals(db, plant)
    items = forecast_history(db, plant, days)
    scored = [i for i in items if i["actual_kwh"] is not None]
    mae = round(sum(abs(i["error_kwh"]) for i in scored) / len(scored), 3) if scored else None
    active = get_active(db, plant.id)
    return {
        "plant_id": plant.id,
        "active_model_id": active.id if active else None,
        "items": items,
        "live_forecast_mae_kwh": mae,
        "scored_days": len(scored),
        "note": "Live MAE scores forecasts made with forecast weather against actuals - the honest measure of real forecasting skill.",
    }


@router.get("/forecasts/backtest")
def backtest(plant: PlantDep, db: DB, start: date | None = None, end: date | None = None) -> dict:
    """Actual vs model-expected for past days using OBSERVED weather (model quality, not forecast quality)."""
    from datetime import timedelta

    from app.services.analytics import plant_today

    end = end or plant_today(plant) - timedelta(days=1)
    start = start or end - timedelta(days=60)
    dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    try:
        res = expected_for_dates(db, plant, dates)
    except NoActiveModel as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    from app.ml.inference import _history

    hist = _history(db, plant).set_index("date")["generation_kwh"]
    items = []
    for d, e in res["expected"].items():
        a = float(hist.loc[d]) if d in hist.index else None
        items.append(
            {
                "date": d.isoformat(),
                "expected_kwh": round(e, 3),
                "actual_kwh": a,
                "residual_kwh": round(a - e, 3) if a is not None else None,
            }
        )
    return {
        "plant_id": plant.id,
        "model_version_id": res["model_version_id"],
        "algorithm": res["algorithm"],
        "weather_input_kind": "observed",
        "items": items,
    }


# --------------------------------------------------------------------------- anomalies


class AnomalyStatusUpdate(BaseModel):
    status: AnomalyStatus


def _anomaly_out(a: Anomaly) -> dict[str, Any]:
    return {
        "id": a.id,
        "date": a.date.isoformat(),
        "category": a.category,
        "detector": a.detector,
        "severity": a.severity.value,
        "status": a.status.value,
        "title": a.title,
        "description": a.description,
        "actual_kwh": a.actual_kwh,
        "expected_kwh": a.expected_kwh,
        "deviation_kwh": a.deviation_kwh,
        "deviation_pct": a.deviation_pct,
        "score": a.score,
        "evidence": a.evidence,
        "recommended_checks": a.recommended_checks,
        "model_version_id": a.model_version_id,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


@router.post("/anomalies/detect")
def detect(
    plant: PlantDep, db: DB, lookback_days: int = Query(60, ge=7, le=400), include_synthetic: bool = False
) -> dict:
    return to_jsonable(
        anomaly_svc.detect_anomalies(
            db, plant, lookback_days=lookback_days, include_synthetic=include_synthetic
        )
    )


@router.get("/anomalies")
def list_anomalies(
    plant: PlantDep,
    db: DB,
    status_filter: AnomalyStatus | None = Query(None, alias="status"),
    severity: AnomalySeverity | None = None,
    category: str | None = None,
    start: date | None = None,
    end: date | None = None,
    limit: int = Query(200, le=1000),
) -> dict:
    stmt = select(Anomaly).where(Anomaly.plant_id == plant.id)
    if status_filter:
        stmt = stmt.where(Anomaly.status == status_filter)
    if severity:
        stmt = stmt.where(Anomaly.severity == severity)
    if category:
        stmt = stmt.where(Anomaly.category == category)
    if start:
        stmt = stmt.where(Anomaly.date >= start)
    if end:
        stmt = stmt.where(Anomaly.date <= end)
    rows = db.scalars(stmt.order_by(desc(Anomaly.date), desc(Anomaly.severity)).limit(limit)).all()
    counts: dict[str, int] = {}
    for a in rows:
        counts[a.severity.value] = counts.get(a.severity.value, 0) + 1
    return {
        "plant_id": plant.id,
        "count": len(rows),
        "by_severity": counts,
        "items": [_anomaly_out(a) for a in rows],
    }


@router.get("/anomalies/{anomaly_id}")
def get_anomaly(anomaly_id: int, plant: PlantDep, db: DB) -> dict:
    a = db.get(Anomaly, anomaly_id)
    if a is None or a.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomaly not found")
    return _anomaly_out(a)


@router.patch("/anomalies/{anomaly_id}")
def update_anomaly(anomaly_id: int, payload: AnomalyStatusUpdate, plant: PlantDep, db: DB) -> dict:
    a = db.get(Anomaly, anomaly_id)
    if a is None or a.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Anomaly not found")
    return _anomaly_out(anomaly_svc.set_status(db, a, payload.status))
