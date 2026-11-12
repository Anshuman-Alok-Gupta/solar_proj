"""Model artifact persistence (joblib) and ModelVersion bookkeeping."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ModelMetric, ModelVersion


def artifact_dir(plant_id: int) -> Path:
    d = get_settings().model_dir / f"plant_{plant_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def dataset_fingerprint(frame_json: str) -> str:
    return hashlib.sha256(frame_json.encode("utf-8")).hexdigest()[:16]


def save_artifact(plant_id: int, tag: str, payload: dict[str, Any]) -> Path:
    path = artifact_dir(plant_id) / f"{tag}.joblib"
    joblib.dump(payload, path)
    return path


def load_artifact(path: str | Path) -> dict[str, Any]:
    return joblib.load(path)


def register_model(
    db: Session,
    *,
    plant_id: int,
    algorithm: str,
    tag: str,
    metrics: dict[str, dict[str, float]],
    feature_names: list[str],
    hyperparameters: dict[str, Any],
    splits: dict[str, Any],
    artifact_path: Path | None,
    feature_importance: dict[str, float] | None,
    fingerprint: str,
    row_counts: dict[str, int],
    data_sources: list[str],
    is_baseline: bool,
    notes: str | None = None,
) -> ModelVersion:
    mv = ModelVersion(
        plant_id=plant_id,
        task="next_day_kwh",
        algorithm=algorithm,
        version_tag=tag,
        trained_at=datetime.now(UTC),
        training_start=splits.get("train_start"),
        training_end=splits.get("train_end"),
        validation_start=splits.get("val_start"),
        test_start=splits.get("test_start"),
        test_end=splits.get("test_end"),
        feature_names=feature_names,
        hyperparameters=json.loads(json.dumps(hyperparameters, default=str)),
        metrics=metrics,
        feature_importance=feature_importance,
        artifact_path=str(artifact_path) if artifact_path else None,
        dataset_fingerprint=fingerprint,
        row_counts=row_counts,
        data_sources=data_sources,
        is_active=False,
        is_baseline=is_baseline,
        notes=notes,
    )
    db.add(mv)
    db.flush()
    for split, vals in metrics.items():
        for name, value in vals.items():
            if value is None:
                continue
            db.add(ModelMetric(model_version_id=mv.id, split=split, metric_name=name, value=float(value)))
    return mv


def set_active(db: Session, plant_id: int, model_version_id: int) -> None:
    db.execute(update(ModelVersion).where(ModelVersion.plant_id == plant_id).values(is_active=False))
    db.execute(update(ModelVersion).where(ModelVersion.id == model_version_id).values(is_active=True))


def get_active(db: Session, plant_id: int) -> ModelVersion | None:
    return db.scalar(
        select(ModelVersion).where(ModelVersion.plant_id == plant_id, ModelVersion.is_active.is_(True))
    )
