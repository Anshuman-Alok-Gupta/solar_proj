"""SHAP-based explanations (global importance and per-prediction contributions)."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_FEATURE_LABELS = {
    "gti_wh": "Tilted-plane irradiance (Wh/m²)",
    "ghi_mj": "Horizontal irradiance (MJ/m²)",
    "sunshine_h": "Sunshine hours",
    "cloud_cover": "Cloud cover (%)",
    "temp_max": "Max temperature (°C)",
    "temp_mean": "Mean temperature (°C)",
    "humidity": "Humidity (%)",
    "wind_max": "Max wind (km/h)",
    "precip": "Precipitation (mm)",
    "doy_sin": "Season (sin)",
    "doy_cos": "Season (cos)",
    "day_length_proxy": "Day length (h)",
    "gen_lag1": "Yesterday's generation",
    "gen_lag2": "Generation 2 days ago",
    "gen_lag3": "Generation 3 days ago",
    "gen_lag7": "Generation 7 days ago",
    "gen_roll7_mean": "7-day mean generation",
    "gen_roll14_mean": "14-day mean generation",
    "gen_roll7_std": "7-day generation variability",
    "pr_roll7": "Recent performance ratio",
}


def label(feature: str) -> str:
    return _FEATURE_LABELS.get(feature, feature)


def _tree_model_and_matrix(model, X: pd.DataFrame):
    """Return (estimator, numeric matrix) handling the sklearn imputer pipeline."""
    if hasattr(model, "named_steps"):
        imputed = model.named_steps["impute"].transform(X)
        return model.named_steps["model"], pd.DataFrame(imputed, columns=X.columns, index=X.index)
    return model, X


def shap_values(artifact: dict[str, Any], X: pd.DataFrame) -> tuple[np.ndarray, float] | None:
    if artifact["algorithm"].startswith("baseline"):
        return None
    try:
        import shap

        est, mat = _tree_model_and_matrix(artifact["model"], X)
        explainer = shap.TreeExplainer(est)
        vals = explainer.shap_values(mat)
        base = explainer.expected_value
        if isinstance(base, (list, np.ndarray)):
            base = float(np.asarray(base).ravel()[0])
        return np.asarray(vals), float(base)
    except Exception as exc:  # pragma: no cover
        log.warning("SHAP failed for %s: %s", artifact["algorithm"], exc)
        return None


def global_importance(artifact: dict[str, Any], X: pd.DataFrame) -> dict[str, Any] | None:
    res = shap_values(artifact, X)
    if res is None:
        return None
    vals, base = res
    mean_abs = np.abs(vals).mean(axis=0)
    items = sorted(
        (
            {"feature": f, "label": label(f), "mean_abs_shap": round(float(v), 4)}
            for f, v in zip(X.columns, mean_abs, strict=True)
        ),
        key=lambda d: -d["mean_abs_shap"],
    )
    # beeswarm-style raw values for the UI (feature value + shap per row), capped
    rows = []
    for i in range(min(len(X), 120)):
        rows.append(
            {
                "row": i,
                "values": {
                    f: (None if pd.isna(X.iloc[i][f]) else round(float(X.iloc[i][f]), 4)) for f in X.columns
                },
                "shap": {f: round(float(vals[i, j]), 4) for j, f in enumerate(X.columns)},
            }
        )
    return {"base_value": round(base, 4), "features": items, "samples": rows, "n": int(len(X))}


def explain_row(artifact: dict[str, Any], X_row: pd.DataFrame, top: int = 8) -> dict[str, Any] | None:
    res = shap_values(artifact, X_row)
    if res is None:
        return None
    vals, base = res
    contribs = [
        {
            "feature": f,
            "label": label(f),
            "value": (None if pd.isna(X_row.iloc[0][f]) else round(float(X_row.iloc[0][f]), 3)),
            "contribution_kwh": round(float(vals[0, j]), 3),
        }
        for j, f in enumerate(X_row.columns)
    ]
    contribs.sort(key=lambda c: -abs(c["contribution_kwh"]))
    return {
        "base_value_kwh": round(base, 3),
        "top_contributions": contribs[:top],
        "method": "SHAP TreeExplainer (additive contributions around the model's average prediction)",
    }
