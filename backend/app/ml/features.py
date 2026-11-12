"""Feature engineering for next-day generation forecasting.

The row for target date *d* contains:
  * weather for day d (observed when training/backtesting, *forecast* when predicting ahead)
  * calendar features for d
  * lagged generation known at the end of day d-1 (lags 1, 2, 3, 7; rolling means/std)
  * a plant performance ratio proxy: rolling mean of generation / GTI

No feature uses information from day d other than its weather, so a model trained here
is usable for genuine next-day forecasting *provided* the weather inputs come from a
forecast. Backtests on observed weather measure the model, not the weather forecast.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

WEATHER_FEATURES = [
    "gti_wh",
    "ghi_mj",
    "sunshine_h",
    "cloud_cover",
    "temp_max",
    "temp_mean",
    "humidity",
    "wind_max",
    "precip",
]
CALENDAR_FEATURES = ["doy_sin", "doy_cos", "day_length_proxy"]
LAG_FEATURES = [
    "gen_lag1",
    "gen_lag2",
    "gen_lag3",
    "gen_lag7",
    "gen_roll7_mean",
    "gen_roll14_mean",
    "gen_roll7_std",
    "pr_roll7",
]
ALL_FEATURES = WEATHER_FEATURES + CALENDAR_FEATURES + LAG_FEATURES
TARGET = "generation_kwh"


def weather_rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    """Rows: dicts with date, shortwave_radiation_sum, gti_sum_wh_m2, sunshine_duration_s, cloud_cover_mean,
    temperature_max, temperature_mean, humidity_mean, wind_speed_max, precipitation_sum."""
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["date"] + WEATHER_FEATURES)
    out = pd.DataFrame(
        {
            "date": pd.to_datetime(df["date"]).dt.date,
            "gti_wh": df.get("gti_sum_wh_m2"),
            "ghi_mj": df.get("shortwave_radiation_sum"),
            "sunshine_h": pd.to_numeric(df.get("sunshine_duration_s"), errors="coerce") / 3600.0,
            "cloud_cover": df.get("cloud_cover_mean"),
            "temp_max": df.get("temperature_max"),
            "temp_mean": df.get("temperature_mean"),
            "humidity": df.get("humidity_mean"),
            "wind_max": df.get("wind_speed_max"),
            "precip": df.get("precipitation_sum"),
        }
    )
    return out


def add_calendar(df: pd.DataFrame, latitude: float | None) -> pd.DataFrame:
    d = pd.to_datetime(df["date"])
    doy = d.dt.dayofyear
    df["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    lat = np.radians(latitude if latitude is not None else 20.0)
    decl = np.radians(23.44) * np.sin(2 * np.pi * (284 + doy) / 365.25)
    x = np.clip(-np.tan(lat) * np.tan(decl), -1, 1)
    df["day_length_proxy"] = 24 / np.pi * np.arccos(x)
    return df


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    """Lag features computed on a daily-continuous index so gaps don't leak future values."""
    df = df.sort_values("date").reset_index(drop=True)
    full = pd.DataFrame({"date": pd.date_range(df["date"].min(), df["date"].max(), freq="D").date})
    df = full.merge(df, on="date", how="left")
    g = df[TARGET]
    df["gen_lag1"] = g.shift(1)
    df["gen_lag2"] = g.shift(2)
    df["gen_lag3"] = g.shift(3)
    df["gen_lag7"] = g.shift(7)
    df["gen_roll7_mean"] = g.shift(1).rolling(7, min_periods=3).mean()
    df["gen_roll14_mean"] = g.shift(1).rolling(14, min_periods=5).mean()
    df["gen_roll7_std"] = g.shift(1).rolling(7, min_periods=3).std()
    pr = (g / df["gti_wh"].replace(0, np.nan) * 1000).where(df["gti_wh"] > 500)
    df["pr_roll7"] = pr.shift(1).rolling(7, min_periods=3).mean()
    return df


def build_training_frame(daily: pd.DataFrame, weather: pd.DataFrame, latitude: float | None) -> pd.DataFrame:
    """daily: columns date, generation_kwh, is_complete. weather: from weather_rows_to_frame."""
    d = daily[daily["is_complete"]][["date", TARGET]].copy()
    df = d.merge(weather, on="date", how="left")
    df = add_calendar(df, latitude)
    df = add_lags(df)
    df = df[df[TARGET].notna()]
    return df.reset_index(drop=True)


def build_inference_row(
    history: pd.DataFrame, weather_for_target: dict, target: date, latitude: float | None
) -> pd.DataFrame:
    """One feature row for `target` using history (date, generation_kwh, gti_wh...) up to target-1."""
    hist = (
        history[["date", TARGET, "gti_wh"]].copy()
        if "gti_wh" in history
        else history[["date", TARGET]].assign(gti_wh=np.nan)
    )
    hist = hist[hist["date"] < target]
    wrow = weather_rows_to_frame([{**weather_for_target, "date": target}])
    row = wrow.copy()
    row[TARGET] = np.nan
    frame = pd.concat([hist.merge(wrow[["date"]], on="date", how="left"), row], ignore_index=True)
    frame = add_calendar(frame, latitude)
    frame = add_lags(frame)
    last = frame[frame["date"] == target].tail(1).copy()
    for c in WEATHER_FEATURES:
        last[c] = wrow[c].iloc[0] if c in wrow else np.nan
    return last
