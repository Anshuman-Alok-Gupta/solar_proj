"""Model artifact persistence (joblib) and ModelVersion bookkeeping.

Artifacts are written to MODEL_DIR *and* stored in the database (model_artifacts). The file is
a cache: on hosts with an ephemeral disk (Render free, containers) a missing file is restored
from the database on first use.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy import select, update
from sqlalchemy.orm import Session, object_session

from app.core.config import get_settings
from app.db.models import ModelArtifact, ModelMetric, ModelVersion

log = logging.getLogger(__name__)


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


class ArtifactMissing(FileNotFoundError):
    """Neither the cached file nor a stored copy exists for this model version."""


def store_artifact_bytes(db: Session, model_version_id: int, data: bytes) -> None:
    db.merge(ModelArtifact(model_version_id=model_version_id, data=data, size_bytes=len(data)))


def load_artifact(mv: ModelVersion) -> dict[str, Any]:
    """Load a model version's artifact from the file cache, else from the database."""
    path = Path(mv.artifact_path) if mv.artifact_path else None
    if path is not None and path.is_file():
        return joblib.load(path)
    db = object_session(mv)
    blob = db.get(ModelArtifact, mv.id) if db is not None else None
    if blob is None:
        raise ArtifactMissing(f"No artifact stored for model version {mv.id} ({mv.algorithm}); retrain models")
    # Re-cache under this host's MODEL_DIR (the stored path may come from another machine).
    name = path.name if path is not None else f"{mv.version_tag}.joblib"
    cache = artifact_dir(mv.plant_id) / name
    try:
        cache.write_bytes(blob.data)
    except OSError as exc:  # pragma: no cover - read-only disk
        log.warning("Could not cache artifact %s: %s", cache, exc)
    return joblib.load(io.BytesIO(blob.data))


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
    if artifact_path is not None:
        store_artifact_bytes(db, mv.id, Path(artifact_path).read_bytes())
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
