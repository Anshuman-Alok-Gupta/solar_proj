"""ML pipeline tests: leakage guard, chronological splits, baselines, training, inference, anomalies."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.ml.features import (
    ALL_FEATURES,
    LAG_FEATURES,
    TARGET,
    add_lags,
    build_training_frame,
    weather_rows_to_frame,
)
from app.ml.train import expanding_folds, metrics, train_models
from tests.conftest import seed_days


def test_lag_features_never_use_same_day_or_future():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]
    daily = pd.DataFrame({"date": days, TARGET: np.arange(30, dtype=float), "is_complete": True})
    weather = weather_rows_to_frame(
        [{"date": d, "gti_sum_wh_m2": 5000, "shortwave_radiation_sum": 18} for d in days]
    )
    frame = build_training_frame(daily, weather, latitude=18.5)
    # Row for day i has generation i. gen_lag1 must be i-1, roll7 mean over i-7..i-1.
    row = frame[frame["date"] == days[20]].iloc[0]
    assert row["gen_lag1"] == 19 and row["gen_lag7"] == 13
    assert row["gen_roll7_mean"] == pytest.approx(np.mean(range(13, 20)))
    # Perturb the target on day 20 and verify no feature of day 20 changes (no same-day leakage)
    daily2 = daily.copy()
    daily2.loc[daily2["date"] == days[20], TARGET] = 999
    frame2 = build_training_frame(daily2, weather, latitude=18.5)
    r1 = frame[frame["date"] == days[20]][LAG_FEATURES].iloc[0]
    r2 = frame2[frame2["date"] == days[20]][LAG_FEATURES].iloc[0]
    pd.testing.assert_series_equal(r1, r2, check_names=False)
    # but the next day's lag1 does change
    assert frame2[frame2["date"] == days[21]]["gen_lag1"].iloc[0] == 999


def test_add_lags_respects_date_gaps():
    days = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)]
    df = pd.DataFrame({"date": days, TARGET: [1.0, 2.0, 3.0], "gti_wh": [5000, 5000, 5000]})
    out = add_lags(df)
    row = out[out["date"] == date(2026, 1, 5)].iloc[0]
    assert np.isnan(row["gen_lag1"])  # Jan 4 missing -> NaN, not Jan 2's value


def test_expanding_folds_are_chronological():
    folds = expanding_folds(50, 5, min_train=20, min_val=5)
    assert len(folds) == 5
    prev_end = 0
    for tr, va in folds:
        assert tr.max() < va.min()
        assert va.min() >= prev_end
        prev_end = va.max()
    assert folds[-1][1].max() == 49


def test_metrics_basic():
    m = metrics(np.array([1, 2, 3]), np.array([1, 2, 3]))
    assert m["mae"] == 0 and m["r2"] == 1
    m = metrics(np.array([1, 2, 3, 4]), np.array([2, 2, 2, 2]))
    assert m["mae"] == 1.0 and m["n"] == 4


def test_training_requires_minimum_rows(db, client, auth, plant):
    from app.db.models import Plant

    p = db.get(Plant, plant["id"])
    with pytest.raises(ValueError):
        train_models(db, p)


def test_end_to_end_training_forecast_and_anomalies(db, client, auth, plant):
    from app.db.models import Plant

    pid = plant["id"]
    seed_days(db, pid, n=70)
    p = db.get(Plant, pid)
    res = train_models(
        db, p, algorithms=["baseline_persistence", "baseline_irradiance", "random_forest", "lightgbm"]
    )
    assert res["rows"] >= 60 and res["test_size"] >= 7
    assert set(ALL_FEATURES) >= set(res["features"])
    for r in res["results"]:
        assert r["validation"]["mae"] is not None and r["test"]["mae"] is not None
    assert res["selected"]["model_version_id"] is not None

    # API: model registry + explain + backtest
    r = client.get(f"/api/plants/{pid}/ml/models", headers=auth)
    assert r.status_code == 200 and r.json()["active_model_id"] == res["selected"]["model_version_id"]
    r = client.get(f"/api/plants/{pid}/forecasts/backtest", headers=auth)
    assert r.status_code == 200 and len(r.json()["items"]) > 0
    r = client.get(f"/api/plants/{pid}/ml/explain", headers=auth)
    assert r.status_code == 200

    # forecasts need forecast weather: add some
    from datetime import UTC, datetime

    from app.db.models import WeatherDaily, WeatherKind
    from app.services.analytics import plant_today

    today = plant_today(p)
    for i in range(1, 4):
        db.add(
            WeatherDaily(
                plant_id=pid,
                date=today + timedelta(days=i),
                kind=WeatherKind.FORECAST,
                issued_at=datetime.now(UTC),
                source="test",
                gti_sum_wh_m2=5500,
                shortwave_radiation_sum=19,
                cloud_cover_mean=30,
                temperature_max=30,
                temperature_mean=26,
                sunshine_duration_s=30000,
                humidity_mean=50,
                wind_speed_max=10,
                precipitation_sum=0,
            )
        )
    db.commit()
    r = client.post(f"/api/plants/{pid}/forecasts/issue?days=3", headers=auth)
    assert r.status_code == 200, r.text
    issued = r.json()["issued"]
    assert len(issued) == 3 and all(f["predicted_kwh"] >= 0 for f in issued)
    assert all(f["weather_input_kind"] == "forecast" for f in issued)

    # anomalies: the seeded drop day (n-20) should be flagged
    r = client.post(f"/api/plants/{pid}/anomalies/detect", headers=auth)
    assert r.status_code == 200, r.text
    r = client.get(f"/api/plants/{pid}/anomalies", headers=auth)
    cats = {a["category"] for a in r.json()["items"]}
    assert "unexpected_production_drop" in cats or "zero_generation" in cats
    first = r.json()["items"][0]
    assert first["recommended_checks"] and first["evidence"] is not None
    # status update
    r = client.patch(
        f"/api/plants/{pid}/anomalies/{first['id']}", headers=auth, json={"status": "acknowledged"}
    )
    assert r.status_code == 200 and r.json()["status"] == "acknowledged"


def test_artifact_restored_from_db_when_file_missing(db, client, auth, plant):
    """Ephemeral-disk hosts: artifacts survive the loss of MODEL_DIR via model_artifacts."""
    from pathlib import Path

    from app.db.models import ModelArtifact, ModelVersion, Plant
    from app.ml.inference import NoActiveModel, expected_for_dates
    from app.ml.registry import get_active

    pid = plant["id"]
    days = seed_days(db, pid, n=70)
    p = db.get(Plant, pid)
    train_models(db, p, algorithms=["baseline_irradiance", "random_forest"])
    db.commit()
    mv = get_active(db, pid)
    blob = db.get(ModelArtifact, mv.id)
    assert blob is not None and blob.size_bytes == len(blob.data) > 0

    for v in db.query(ModelVersion).filter(ModelVersion.plant_id == pid):
        Path(v.artifact_path).unlink()  # simulate a fresh container
    res = expected_for_dates(db, p, days[-5:], model=mv)
    assert len(res["expected"]) > 0
    assert Path(mv.artifact_path).is_file()  # re-cached

    # no stored copy and no file -> clean NoActiveModel, not a FileNotFoundError
    Path(mv.artifact_path).unlink()
    db.delete(blob)
    db.flush()
    with pytest.raises(NoActiveModel):
        expected_for_dates(db, p, days[-5:], model=mv)
