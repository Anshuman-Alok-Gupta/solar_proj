"""Nightly ML upkeep: retrain when data changed, keep stable choice, prune old artifacts."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import ModelArtifact, ModelVersion
from app.ml.nightly import _choose
from app.ml.train import ALGORITHMS
from tests.conftest import seed_days


def _r(mid, algo, mae):
    return {"model_version_id": mid, "algorithm": algo, "validation": {"mae": mae}}


def test_choose_keeps_active_algorithm_when_close():
    results = [_r(1, "baseline_irradiance", 1.00), _r(2, "lightgbm", 1.04), _r(3, "xgboost", 1.5)]
    assert _choose(results, 1, "lightgbm")[0] == 2  # within 5 %: keep lightgbm (retrained)
    assert _choose(results, 1, "xgboost")[0] == 1  # clearly worse: switch
    assert _choose(results, 1, None)[0] == 1
    assert _choose(results, 1, "baseline_irradiance")[0] == 1


def test_nightly_run_trains_skips_without_new_data_and_prunes(client, auth, plant, db):
    pid = plant["id"]
    seed_days(db, pid, n=70)
    url = f"/api/plants/{pid}/ml/nightly-run"

    first = client.post(url, headers=auth).json()
    assert first["status"] in ("success", "partial"), first
    train = first["details"]["train"]
    assert train["active"]["algorithm"] in ALGORITHMS and train["rows"] >= 20
    assert "anomaly detection done" in first["message"]

    second = client.post(url, headers=auth).json()
    assert second["details"]["train"] == {"skipped": "no new data since the last training run"}

    # two more forced runs -> 3 runs stored, only the latest 2 (+ active) keep their files
    for _ in range(2):
        assert client.post(url + "?force_train=true", headers=auth).json()["details"]["train"]["active"]
    n_versions = db.scalar(select(func.count()).select_from(ModelVersion).where(ModelVersion.plant_id == pid))
    n_artifacts = db.scalar(select(func.count()).select_from(ModelArtifact))
    assert n_versions == 3 * len(ALGORITHMS)
    assert 2 * len(ALGORITHMS) <= n_artifacts <= 2 * len(ALGORITHMS) + 1
    active = db.scalar(
        select(ModelVersion).where(ModelVersion.plant_id == pid, ModelVersion.is_active.is_(True))
    )
    assert db.get(ModelArtifact, active.id) is not None

    status = client.get(f"/api/plants/{pid}/ml/nightly-status", headers=auth).json()
    assert status["schedule"].startswith("daily at 21:00") and status["last_run"]["status"] in (
        "success",
        "partial",
    )
