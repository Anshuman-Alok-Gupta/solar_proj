"""Nightly ML upkeep: retrain on the latest data, issue forecasts, detect anomalies.

Runs once a night (after sunset, see ``ML_NIGHTLY_*`` settings) for every real plant and is
logged in ``sync_logs`` (source ``ml``, mode ``nightly``).

* Training is skipped when the training data has not changed since the last run (same
  dataset fingerprint), e.g. during a SEMS outage.
* All algorithms are retrained on the same splits and the best by validation MAE is chosen
  with the usual policy (``select_model``). For stability the currently active algorithm is
  kept (in its retrained form) when it is within ``KEEP_ACTIVE_TOLERANCE`` of the best.
* Model files are pruned: the active model and the latest ``KEEP_RUNS`` runs keep their
  artifacts; older versions keep their metrics/history rows but lose the stored file, so the
  free database does not fill up (~1 MB per run).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import Session

from app.db.models import ModelArtifact, ModelVersion, Plant, SyncLog, SyncStatus
from app.ml import anomaly as anomaly_svc
from app.ml.inference import NoActiveModel, issue_forecasts
from app.ml.registry import dataset_fingerprint, get_active, set_active
from app.ml.train import ALGORITHMS, MIN_ROWS, TARGET, load_training_data, train_models

log = logging.getLogger(__name__)

KEEP_ACTIVE_TOLERANCE = 0.05
KEEP_RUNS = 2


def _choose(results: list[dict[str, Any]], selected_id: int, active_algorithm: str | None) -> tuple[int, str]:
    """Prefer the retrained active algorithm when it is within tolerance of the policy's pick."""
    by_id = {r["model_version_id"]: r for r in results}
    best = by_id.get(selected_id)
    if best is None or active_algorithm is None or best["algorithm"] == active_algorithm:
        return selected_id, "policy pick"
    same = next((r for r in results if r["algorithm"] == active_algorithm), None)
    b_mae = (best.get("validation") or {}).get("mae")
    s_mae = (same or {}).get("validation", {}).get("mae") if same else None
    if same and b_mae is not None and s_mae is not None and s_mae <= b_mae * (1 + KEEP_ACTIVE_TOLERANCE):
        return same["model_version_id"], (
            f"kept {active_algorithm} (validation MAE {s_mae} within {KEEP_ACTIVE_TOLERANCE:.0%} of "
            f"{best['algorithm']} {b_mae})"
        )
    return (
        selected_id,
        f"switched from {active_algorithm} to {best['algorithm']} (validation MAE {b_mae} vs {s_mae})",
    )


def prune_artifacts(db: Session, plant: Plant, keep_runs: int = KEEP_RUNS) -> int:
    """Drop stored model files for versions older than the latest runs (never the active one)."""
    keep_n = keep_runs * len(ALGORITHMS)
    versions = db.scalars(
        select(ModelVersion).where(ModelVersion.plant_id == plant.id).order_by(desc(ModelVersion.trained_at))
    ).all()
    old = [v for v in versions[keep_n:] if not v.is_active]
    if not old:
        return 0
    ids = [v.id for v in old]
    removed = db.execute(delete(ModelArtifact).where(ModelArtifact.model_version_id.in_(ids))).rowcount or 0
    for v in old:
        if v.artifact_path:
            try:
                Path(v.artifact_path).unlink(missing_ok=True)
            except OSError:
                pass
    db.commit()
    return removed


def run_nightly(db: Session, plant: Plant, *, force_train: bool = False) -> SyncLog:
    log_row = SyncLog(plant_id=plant.id, source="ml", mode="nightly", status=SyncStatus.RUNNING)
    db.add(log_row)
    db.commit()
    details: dict[str, Any] = {}
    errors: list[str] = []
    parts: list[str] = []

    # 1. retrain (unless nothing changed)
    try:
        frame, _ = load_training_data(db, plant)
        if len(frame) < MIN_ROWS:
            details["train"] = {"skipped": f"only {len(frame)} usable days (need {MIN_ROWS})"}
            parts.append("training skipped (not enough data)")
        else:
            fp = dataset_fingerprint(frame[["date", TARGET]].to_json(default_handler=str))
            last_fp = db.scalar(
                select(ModelVersion.dataset_fingerprint)
                .where(ModelVersion.plant_id == plant.id)
                .order_by(desc(ModelVersion.trained_at))
                .limit(1)
            )
            if fp == last_fp and not force_train:
                details["train"] = {"skipped": "no new data since the last training run"}
                parts.append("training skipped (no new data)")
            else:
                prev = get_active(db, plant.id)
                prev_algo = prev.algorithm if prev else None
                res = train_models(db, plant, activate_best=False)
                chosen_id, why = _choose(res["results"], res["selected"]["model_version_id"], prev_algo)
                set_active(db, plant.id, chosen_id)
                db.commit()
                chosen = next(r for r in res["results"] if r["model_version_id"] == chosen_id)
                details["train"] = {
                    "rows": res["rows"],
                    "active": {"algorithm": chosen["algorithm"], "model_version_id": chosen_id},
                    "previous_algorithm": prev_algo,
                    "decision": why,
                    "validation_mae": (chosen.get("validation") or {}).get("mae"),
                    "test_mae": (chosen.get("test") or {}).get("mae"),
                }
                details["pruned_artifacts"] = prune_artifacts(db, plant)
                parts.append(
                    f"retrained {len(res['results'])} models on {res['rows']} days, active {chosen['algorithm']}"
                )
    except Exception as exc:
        db.rollback()
        errors.append(f"training: {type(exc).__name__}: {exc}")
        log.exception("Nightly training failed for plant %s", plant.id)

    # 2. forecasts for the next days
    try:
        fc = issue_forecasts(db, plant, days=3)
        details["forecasts"] = len(fc.get("issued", []))
        parts.append(f"{details['forecasts']} forecast(s) issued")
    except NoActiveModel:
        details["forecasts"] = "no active model"
    except Exception as exc:
        db.rollback()
        errors.append(f"forecasts: {type(exc).__name__}: {exc}")
        log.exception("Nightly forecasts failed for plant %s", plant.id)

    # 3. anomaly detection
    try:
        an = anomaly_svc.detect_anomalies(db, plant)
        details["anomalies"] = {k: an.get(k) for k in ("created_or_updated", "by_category") if k in an}
        parts.append("anomaly detection done")
    except Exception as exc:
        db.rollback()
        errors.append(f"anomalies: {type(exc).__name__}: {exc}")
        log.exception("Nightly anomaly detection failed for plant %s", plant.id)

    log_row.status = (
        SyncStatus.SUCCESS if not errors else (SyncStatus.PARTIAL if parts else SyncStatus.FAILED)
    )
    log_row.message = "; ".join(parts) or "nothing done"
    log_row.error = " | ".join(errors) or None
    log_row.details = details
    log_row.finished_at = datetime.now(UTC)
    db.add(log_row)
    db.commit()
    return log_row


def run_nightly_all(db: Session) -> list[SyncLog]:
    plants = db.scalars(select(Plant).where(Plant.is_demo.is_(False))).all()
    return [run_nightly(db, p) for p in plants]
