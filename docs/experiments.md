# Experiments log

All numbers below were produced by `POST /api/plants/1/ml/train` on real data and are
stored in `model_versions` / `model_metrics`. Re-run training to refresh them; this file
records the state on the dates given.

## Dataset

| Item | Value |
|---|---|
| Plant | Household rooftop, 5 kW DC array, GoodWe GW6000-SDT-20 (6 kW AC), southwest facing, Pune region |
| Generation source | GoodWe SEMS+ daily statistics (`sems_api`), 2026-09-12 → 2026-11-10 complete days |
| Rows (2026-11-11) | 60 complete days (61 incl. partial today) |
| Weather | Open-Meteo ERA5 archive + historical-forecast bridge, tilted-plane irradiance with tilt 18° (assumed) / azimuth 45° |
| Intraday | One day of 5-minute data (2026-11-11) from the SEMS "Inverter Operation Report" export; live polling adds readings going forward |

Caveat: the array appears to clip around 15 kWh/day in Oct–Nov (flat plateau on clear days,
peak AC power ≈2.2 kW in November). This makes clear-day output nearly constant and limits
how much weather features can explain; the Pearson r between daily GTI and kWh over the 60
days is only 0.31 (0.37 for GHI), driven mostly by a handful of overcast/outage days.

## Preprocessing

* Only complete days (`is_complete = true`) are used; synthetic rows are excluded.
* Daily weather aggregated from hourly Open-Meteo values (GTI Wh/m² summed from hourly W/m²).
* Lag features computed on a continuous date index so gaps produce NaN rather than leaking.

## Features (20)

Weather for the target day: `gti_wh, ghi_mj, sunshine_h, cloud_cover, temp_max, temp_mean, humidity, wind_max, precip`
Calendar: `doy_sin, doy_cos, day_length_proxy`
History (known at end of day-1): `gen_lag1, gen_lag2, gen_lag3, gen_lag7, gen_roll7_mean, gen_roll14_mean, gen_roll7_std, pr_roll7`

Leakage check: `tests/test_ml.py::test_lag_features_never_use_same_day_or_future` perturbs
the target of day *d* and asserts no feature of day *d* changes.

## Protocol

* Hold-out test = last max(7, 20 %) days (12 days: 2026-10-30 → 2026-11-10).
* Validation = expanding-window CV inside the remaining 48 days (5 folds, 5–9 days each, pooled n=29).
* Baselines on identical folds. Selection: lowest validation MAE; among candidates within 10 % of it, prefer a weather-aware model (documented in the training response as `selected.policy`).
* Deployed model is refit on all rows; reported metrics come from the pre-refit evaluation.

## Results (run of 2026-11-11, 60 days)

| Algorithm | Val MAE | Val RMSE | Val R² | Test MAE | Test RMSE | Test R² |
|---|---|---|---|---|---|---|
| 7-day mean baseline | **1.63** | 3.14 | −0.02 | 2.72 | 3.42 | −0.36 |
| Irradiance-scaled baseline (selected) | 1.77 | 3.35 | −0.16 | **1.19** | **1.38** | **0.78** |
| Random Forest | 1.80 | 3.30 | −0.13 | 1.92 | 2.97 | −0.03 |
| CatBoost | 1.81 | 3.22 | −0.08 | 2.23 | 3.25 | −0.23 |
| Persistence baseline | 1.98 | 4.33 | −0.95 | 2.03 | 3.97 | −0.83 |
| LightGBM | 2.32 | 3.51 | −0.27 | 1.44 | 2.99 | −0.04 |
| XGBoost | 2.54 | 3.86 | −0.55 | 1.97 | 3.10 | −0.12 |

Interpretation:

* With 60 days, no gradient-boosted model beats the simple baselines on validation; the
  negative R² values come from a validation window that contains several outage/overcast
  days (e.g. 2026-10-22 = 0 kWh) which no feature set predicts.
* The irradiance-scaled baseline (recent performance ratio × forecast GTI) was selected
  under the tie-break policy: it is within 10 % of the best validation MAE and is
  weather-aware, which the anomaly detector requires. On the hold-out it is clearly best.
* Expect the ranking to change as data accumulates; the Model Lab re-runs the comparison on demand.

## Forecasting

* Live forecasts use Open-Meteo forecast weather; they are scored against actuals when the
  day completes (`live_forecast_mae_kwh` on `/forecasts`). As of 2026-11-11 no live
  forecast had been scored yet (first forecasts issued that day).
* Interval = 10th–90th percentile of validation residuals added to the point forecast
  (empirical; with n=29 residuals it is indicative only).

## Anomaly detection (2026-11-11)

Detector output on the 60 days: 3 unexpected production drops (2026-09-18, 09-20, 09-26
with 50–80 % shortfall under high irradiance), 1 zero-generation day with usable sunlight
(2026-10-22), 2 statistical outliers (Isolation Forest), plus a low-severity drop on
2026-11-02 (weather partly explains it: cloud 67 %, GTI 2.4 kWh/m²).

## Limitations

* 60 days is far too little for seasonal effects; metrics carry wide uncertainty.
* Tilt angle is assumed (18°); GTI would change with the true tilt.
* No smart meter: consumption/export unmeasured, so savings are a range, not a value.
* Weather is modelled (ERA5 / forecast), not measured on site.
