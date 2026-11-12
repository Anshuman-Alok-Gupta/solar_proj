"""Reproducible training + honest evaluation for next-day generation (kWh).

Protocol
--------
1. Build the daily feature frame (complete days only, observed weather).
2. Chronological split: last max(7, 20%) days = hold-out test; the rest = train+val.
3. Expanding-window cross-validation inside train+val (pooled out-of-sample predictions
   give the validation metrics). Baselines are evaluated on exactly the same folds.
4. Refit on train+val, score the untouched test block.
5. Select the lowest validation MAE (a baseline can win - that is reported honestly).
6. Refit the selected algorithm on all rows for deployment; store the artifact, the
   evaluation predictions/residuals and residual quantiles (for empirical intervals).

Nothing is hard-coded: every metric comes from predictions on data the model did not see.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DailyEnergy, DataSource, Plant, WeatherDaily, WeatherKind
from app.ml.features import ALL_FEATURES, TARGET, build_training_frame, weather_rows_to_frame
from app.ml.registry import dataset_fingerprint, register_model, save_artifact, set_active

log = logging.getLogger(__name__)

MIN_ROWS = 20
RANDOM_STATE = 42


# --------------------------------------------------------------------------- baselines


class PersistenceBaseline:
    name = "baseline_persistence"

    def fit(self, X: pd.DataFrame, y: pd.Series):
        self.fallback_ = float(y.mean())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return X["gen_lag1"].fillna(X["gen_roll7_mean"]).fillna(self.fallback_).to_numpy()


class RollingMeanBaseline:
    name = "baseline_rolling7"

    def fit(self, X: pd.DataFrame, y: pd.Series):
        self.fallback_ = float(y.mean())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return X["gen_roll7_mean"].fillna(X["gen_lag1"]).fillna(self.fallback_).to_numpy()


class IrradianceScaledBaseline:
    """kWh = (recent performance ratio) x GTI. A physics-flavoured baseline using tomorrow's weather."""

    name = "baseline_irradiance"

    def fit(self, X: pd.DataFrame, y: pd.Series):
        ratio = (y / X["gti_wh"].replace(0, np.nan) * 1000).replace([np.inf, -np.inf], np.nan)
        self.global_pr_ = float(ratio.median()) if ratio.notna().any() else 0.0
        self.fallback_ = float(y.mean())
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        pr = X["pr_roll7"].fillna(self.global_pr_)
        pred = pr * X["gti_wh"] / 1000
        return pred.fillna(X["gen_roll7_mean"]).fillna(self.fallback_).to_numpy()


def make_model(algorithm: str):
    if algorithm == "baseline_persistence":
        return PersistenceBaseline(), {}
    if algorithm == "baseline_rolling7":
        return RollingMeanBaseline(), {}
    if algorithm == "baseline_irradiance":
        return IrradianceScaledBaseline(), {}
    if algorithm == "random_forest":
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline

        params = {
            "n_estimators": 400,
            "max_depth": 4,
            "min_samples_leaf": 3,
            "random_state": RANDOM_STATE,
            "n_jobs": -1,
        }
        return Pipeline(
            [("impute", SimpleImputer(strategy="median")), ("model", RandomForestRegressor(**params))]
        ), params
    if algorithm == "xgboost":
        from xgboost import XGBRegressor

        params = {
            "n_estimators": 300,
            "max_depth": 3,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 3,
            "reg_lambda": 2.0,
            "random_state": RANDOM_STATE,
            "n_jobs": 2,
        }
        return XGBRegressor(**params), params
    if algorithm == "lightgbm":
        from lightgbm import LGBMRegressor

        params = {
            "n_estimators": 300,
            "num_leaves": 7,
            "max_depth": 3,
            "learning_rate": 0.05,
            "min_child_samples": 5,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.8,
            "reg_lambda": 2.0,
            "random_state": RANDOM_STATE,
            "verbose": -1,
            "n_jobs": 2,
        }
        return LGBMRegressor(**params), params
    if algorithm == "catboost":
        from catboost import CatBoostRegressor

        params = {
            "iterations": 300,
            "depth": 3,
            "learning_rate": 0.05,
            "l2_leaf_reg": 3.0,
            "random_seed": RANDOM_STATE,
            "verbose": 0,
            "thread_count": 2,
        }
        return CatBoostRegressor(**params), params
    raise ValueError(f"Unknown algorithm {algorithm}")


ALGORITHMS = [
    "baseline_persistence",
    "baseline_rolling7",
    "baseline_irradiance",
    "random_forest",
    "xgboost",
    "lightgbm",
    "catboost",
]
BASELINES = {a for a in ALGORITHMS if a.startswith("baseline")}


# --------------------------------------------------------------------------- metrics


def metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float | None]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    y_true, y_pred = y_true[mask], y_pred[mask]
    if len(y_true) == 0:
        return {"mae": None, "rmse": None, "r2": None, "smape": None, "n": 0}
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    ss_tot = float(np.sum((y_true - y_true.mean()) ** 2))
    r2 = float(1 - np.sum(err**2) / ss_tot) if ss_tot > 0 and len(y_true) > 1 else None
    denom = np.abs(y_true) + np.abs(y_pred)
    smape = float(np.mean(np.where(denom > 0, 2 * np.abs(err) / np.where(denom > 0, denom, 1), 0)) * 100)
    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "r2": round(r2, 4) if r2 is not None else None,
        "smape": round(smape, 2),
        "n": int(len(y_true)),
    }


# --------------------------------------------------------------------------- data


def load_training_data(
    db: Session, plant: Plant, *, include_synthetic: bool = False
) -> tuple[pd.DataFrame, list[str]]:
    sources = [DataSource.SEMS_API, DataSource.CSV_IMPORT] + (
        [DataSource.SYNTHETIC] if include_synthetic else []
    )
    rows = db.execute(
        select(DailyEnergy.date, DailyEnergy.generation_kwh, DailyEnergy.is_complete, DailyEnergy.source)
        .where(DailyEnergy.plant_id == plant.id, DailyEnergy.source.in_(sources))
        .order_by(DailyEnergy.date)
    ).all()
    daily = pd.DataFrame(rows, columns=["date", TARGET, "is_complete", "source"])
    used_sources = sorted({r.source.value for r in rows}) if rows else []
    wrows = (
        db.execute(
            select(
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
            ).where(WeatherDaily.plant_id == plant.id, WeatherDaily.kind == WeatherKind.OBSERVED)
        )
        .mappings()
        .all()
    )
    weather = weather_rows_to_frame([dict(r) for r in wrows])
    if daily.empty:
        return pd.DataFrame(), used_sources
    frame = build_training_frame(daily, weather, plant.latitude)
    return frame, used_sources


# --------------------------------------------------------------------------- evaluation


@dataclass
class EvalResult:
    algorithm: str
    params: dict[str, Any]
    val_metrics: dict[str, Any]
    test_metrics: dict[str, Any]
    train_metrics: dict[str, Any]
    val_predictions: pd.DataFrame
    test_predictions: pd.DataFrame
    feature_importance: dict[str, float] | None = None
    fit_seconds: float = 0.0
    folds: list[dict[str, Any]] = field(default_factory=list)


def expanding_folds(
    n: int, n_folds: int, min_train: int, min_val: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds over indices 0..n-1: validation blocks are contiguous and later than training."""
    usable = n - min_train
    if usable < min_val:
        return []
    n_folds = max(1, min(n_folds, usable // min_val))
    block = usable // n_folds
    folds = []
    start = min_train
    for i in range(n_folds):
        v_start = start + i * block
        v_end = n if i == n_folds - 1 else v_start + block
        folds.append((np.arange(0, v_start), np.arange(v_start, v_end)))
    return folds


def _importance(model, features: list[str]) -> dict[str, float] | None:
    est = model
    if hasattr(model, "named_steps"):
        est = model.named_steps["model"]
    imp = getattr(est, "feature_importances_", None)
    if imp is None:
        return None
    imp = np.asarray(imp, dtype=float)
    total = imp.sum() or 1.0
    return {
        f: round(float(v / total), 5)
        for f, v in sorted(zip(features, imp, strict=True), key=lambda kv: -kv[1])
    }


def evaluate_algorithm(
    algorithm: str, frame: pd.DataFrame, features: list[str], test_size: int, n_folds: int = 5
) -> EvalResult:
    X = frame[features]
    y = frame[TARGET].astype(float)
    n = len(frame)
    n_trainval = n - test_size
    folds = expanding_folds(n_trainval, n_folds, min_train=max(10, int(n_trainval * 0.4)), min_val=5)
    val_rows = []
    fold_info = []
    t0 = time.time()
    for k, (tr, va) in enumerate(folds):
        model, _ = make_model(algorithm)
        model.fit(X.iloc[tr], y.iloc[tr])
        pred = model.predict(X.iloc[va])
        m = metrics(y.iloc[va].to_numpy(), pred)
        fold_info.append(
            {
                "fold": k,
                "train_rows": int(len(tr)),
                "val_rows": int(len(va)),
                "val_start": str(frame["date"].iloc[va[0]]),
                "val_end": str(frame["date"].iloc[va[-1]]),
                **{k2: m[k2] for k2 in ("mae", "rmse")},
            }
        )
        val_rows.append(
            pd.DataFrame(
                {
                    "date": frame["date"].iloc[va].values,
                    "actual": y.iloc[va].values,
                    "predicted": pred,
                    "fold": k,
                }
            )
        )
    val_pred = (
        pd.concat(val_rows, ignore_index=True)
        if val_rows
        else pd.DataFrame(columns=["date", "actual", "predicted", "fold"])
    )
    val_m = (
        metrics(val_pred["actual"].to_numpy(), val_pred["predicted"].to_numpy())
        if not val_pred.empty
        else metrics(np.array([]), np.array([]))
    )

    model, params = make_model(algorithm)
    model.fit(X.iloc[:n_trainval], y.iloc[:n_trainval])
    train_m = metrics(y.iloc[:n_trainval].to_numpy(), model.predict(X.iloc[:n_trainval]))
    test_pred_vals = model.predict(X.iloc[n_trainval:])
    test_pred = pd.DataFrame(
        {
            "date": frame["date"].iloc[n_trainval:].values,
            "actual": y.iloc[n_trainval:].values,
            "predicted": test_pred_vals,
        }
    )
    test_m = metrics(test_pred["actual"].to_numpy(), test_pred["predicted"].to_numpy())
    return EvalResult(
        algorithm=algorithm,
        params=params,
        val_metrics=val_m,
        test_metrics=test_m,
        train_metrics=train_m,
        val_predictions=val_pred,
        test_predictions=test_pred,
        feature_importance=_importance(model, features),
        fit_seconds=round(time.time() - t0, 2),
        folds=fold_info,
    )


# --------------------------------------------------------------------------- selection

SELECTION_TOLERANCE = 0.10  # candidates within 10 % of the best validation MAE are considered tied
WEATHER_BLIND = {"baseline_persistence", "baseline_rolling7"}


def select_model(valid: list[EvalResult]) -> tuple[EvalResult, str]:
    """Lowest validation MAE wins. Among candidates tied within SELECTION_TOLERANCE, prefer a
    weather-aware model, because anomaly detection and forecasting need weather-adjusted
    expectations that persistence / rolling-mean baselines cannot provide. The policy is
    reported with the result and any model can be activated manually in the Model Lab."""
    ranked = sorted(valid, key=lambda r: r.val_metrics["mae"])
    best = ranked[0]
    if best.algorithm not in WEATHER_BLIND:
        return best, "lowest validation MAE"
    limit = best.val_metrics["mae"] * (1 + SELECTION_TOLERANCE)
    tied = [r for r in ranked if r.val_metrics["mae"] <= limit and r.algorithm not in WEATHER_BLIND]
    if tied:
        chosen = tied[0]
        return chosen, (
            f"{chosen.algorithm} selected: within {SELECTION_TOLERANCE:.0%} of the best validation MAE "
            f"({best.algorithm} {best.val_metrics['mae']}) and weather-aware"
        )
    return best, f"lowest validation MAE (no weather-aware model within {SELECTION_TOLERANCE:.0%})"


# --------------------------------------------------------------------------- orchestration


def train_models(
    db: Session,
    plant: Plant,
    *,
    algorithms: list[str] | None = None,
    include_synthetic: bool = False,
    activate_best: bool = True,
) -> dict[str, Any]:
    frame, used_sources = load_training_data(db, plant, include_synthetic=include_synthetic)
    if len(frame) < MIN_ROWS:
        raise ValueError(f"Need at least {MIN_ROWS} complete days with weather to train; have {len(frame)}")
    features = [f for f in ALL_FEATURES if f in frame.columns]
    n = len(frame)
    test_size = max(7, int(round(n * 0.2)))
    n_trainval = n - test_size
    splits = {
        "train_start": frame["date"].iloc[0],
        "train_end": frame["date"].iloc[n_trainval - 1],
        "val_start": None,
        "test_start": frame["date"].iloc[n_trainval],
        "test_end": frame["date"].iloc[-1],
    }
    fingerprint = dataset_fingerprint(frame[["date", TARGET]].to_json(default_handler=str))
    tag_base = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    results: list[EvalResult] = []
    for algo in algorithms or ALGORITHMS:
        try:
            results.append(evaluate_algorithm(algo, frame, features, test_size))
        except Exception as exc:  # pragma: no cover - keep going if one library fails
            log.exception("Training %s failed", algo)
            results.append(
                EvalResult(algo, {}, {"mae": None, "error": str(exc)}, {}, {}, pd.DataFrame(), pd.DataFrame())
            )

    valid = [r for r in results if r.val_metrics.get("mae") is not None]
    if not valid:
        raise RuntimeError("No algorithm could be evaluated")
    best, selection_note = select_model(valid)

    registered: list[dict[str, Any]] = []
    best_mv_id = None
    for r in results:
        if r.val_metrics.get("mae") is None:
            continue
        # Deployment fit on all rows
        model, _ = make_model(r.algorithm)
        model.fit(frame[features], frame[TARGET].astype(float))
        residuals = (
            (r.val_predictions["actual"] - r.val_predictions["predicted"]).to_numpy()
            if not r.val_predictions.empty
            else np.array([])
        )
        artifact = {
            "algorithm": r.algorithm,
            "features": features,
            "model": model,
            "trained_at": datetime.now(UTC).isoformat(),
            "residual_std": float(np.std(residuals)) if len(residuals) > 1 else None,
            "residual_quantiles": {
                q: float(np.quantile(residuals, q)) for q in (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
            }
            if len(residuals) > 4
            else None,
            "val_predictions": r.val_predictions.assign(date=r.val_predictions["date"].astype(str)).to_dict(
                orient="records"
            )
            if not r.val_predictions.empty
            else [],
            "test_predictions": r.test_predictions.assign(
                date=r.test_predictions["date"].astype(str)
            ).to_dict(orient="records")
            if not r.test_predictions.empty
            else [],
            "folds": r.folds,
            "training_rows": int(n),
            "plant_id": plant.id,
        }
        tag = f"{tag_base}_{r.algorithm}"
        path = save_artifact(plant.id, tag, artifact)
        mv = register_model(
            db,
            plant_id=plant.id,
            algorithm=r.algorithm,
            tag=tag,
            metrics={"train": r.train_metrics, "validation": r.val_metrics, "test": r.test_metrics},
            feature_names=features,
            hyperparameters=r.params,
            splits=splits,
            artifact_path=path,
            feature_importance=r.feature_importance,
            fingerprint=fingerprint,
            row_counts={"total": n, "train_val": n_trainval, "test": test_size, "folds": len(r.folds)},
            data_sources=used_sources,
            is_baseline=r.algorithm in BASELINES,
            notes=(
                "Validation = pooled expanding-window CV predictions; test = chronological hold-out. "
                "Deployed model refit on all rows. Backtest uses observed weather - real forecasts will be less accurate."
            ),
        )
        registered.append(
            {
                "model_version_id": mv.id,
                "algorithm": r.algorithm,
                "validation": r.val_metrics,
                "test": r.test_metrics,
                "fit_seconds": r.fit_seconds,
            }
        )
        if r is best:
            best_mv_id = mv.id
    if activate_best and best_mv_id is not None:
        set_active(db, plant.id, best_mv_id)
    db.commit()
    return {
        "plant_id": plant.id,
        "rows": n,
        "features": features,
        "data_sources": used_sources,
        "date_range": {"start": str(frame["date"].iloc[0]), "end": str(frame["date"].iloc[-1])},
        "splits": {k: str(v) if v is not None else None for k, v in splits.items()},
        "test_size": test_size,
        "selected": {
            "algorithm": best.algorithm,
            "model_version_id": best_mv_id,
            "validation_mae": best.val_metrics["mae"],
            "policy": selection_note,
        },
        "results": sorted(
            registered,
            key=lambda r: r["validation"]["mae"] if r["validation"].get("mae") is not None else 1e9,
        ),
        "fingerprint": fingerprint,
        "caveats": [
            f"Only {n} complete days were available; metrics on {test_size} test days carry wide uncertainty.",
            "Backtests use observed weather. Live forecasts use Open-Meteo forecast weather and will be less accurate.",
        ],
    }


def to_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=str))
