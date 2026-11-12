"""Apply a confirmed column mapping: produce normalised interval and/or daily frames.

Output conventions
------------------
interval frame columns: ts (UTC tz-aware), local_date, power_w, energy_kwh, cumulative_energy_kwh,
                        pv_voltage_v, pv_current_a, ac_voltage_v, ac_current_a, frequency_hz, temperature_c
daily frame columns:    date, generation_kwh, peak_power_w, consumption_kwh, exported_kwh, imported_kwh,
                        reading_count, derived_from
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from app.ingestion.inspect import ENERGY_ROLES, ROLES, parse_datetime, to_numeric

ENERGY_FACTORS = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
POWER_FACTORS = {"W": 1.0, "kW": 1000.0}
INTERVAL_COLS = [
    "power_w",
    "energy_kwh",
    "cumulative_energy_kwh",
    "pv_voltage_v",
    "pv_current_a",
    "ac_voltage_v",
    "ac_current_a",
    "frequency_hz",
    "temperature_c",
]
DAILY_COLS = ["generation_kwh", "consumption_kwh", "exported_kwh", "imported_kwh"]


class ColumnRef(BaseModel):
    column: str
    unit: str | None = None


class ColumnMapping(BaseModel):
    columns: dict[str, ColumnRef] = Field(default_factory=dict)
    timezone: str | None = None  # source timezone of naive timestamps; defaults to plant tz
    dayfirst: bool | None = None  # ambiguous dd/mm vs mm/dd

    @field_validator("columns")
    @classmethod
    def _roles_known(cls, v: dict[str, ColumnRef]) -> dict[str, ColumnRef]:
        unknown = [r for r in v if r not in ROLES]
        if unknown:
            raise ValueError(f"Unknown roles: {unknown}")
        return {r: c for r, c in v.items() if r != "ignore"}

    @field_validator("timezone")
    @classmethod
    def _tz_valid(cls, v: str | None) -> str | None:
        if v:
            ZoneInfo(v)
        return v


@dataclass(slots=True)
class NormalizedData:
    kind: str  # "interval" | "daily"
    interval: pd.DataFrame | None
    daily: pd.DataFrame | None
    rejected: pd.DataFrame  # rows that could not be used, with a `reason` column
    notes: list[str] = field(default_factory=list)


class MappingError(ValueError):
    pass


def _convert(series: pd.Series, role: str, unit: str | None) -> pd.Series:
    num = to_numeric(series)
    if role == "power_w":
        return num * POWER_FACTORS.get(unit or "W", 1.0)
    if role in ENERGY_ROLES:
        return num * ENERGY_FACTORS.get(unit or "kWh", 1.0)
    return num


def normalize(df: pd.DataFrame, mapping: ColumnMapping, plant_timezone: str) -> NormalizedData:
    cols = mapping.columns
    if "timestamp" not in cols and "date" not in cols:
        raise MappingError("A 'timestamp' or 'date' column must be mapped")
    for role, ref in cols.items():
        if ref.column not in df.columns:
            raise MappingError(f"Mapped column '{ref.column}' for role '{role}' does not exist in the file")

    tz = ZoneInfo(mapping.timezone or plant_timezone)
    notes: list[str] = []
    time_role = "timestamp" if "timestamp" in cols else "date"
    raw_time = df[cols[time_role].column]
    parsed = parse_datetime(raw_time, mapping.dayfirst)
    reject_mask = parsed.isna()
    rejected_rows: list[dict[str, Any]] = []
    if reject_mask.any():
        for idx in df.index[reject_mask][:500]:
            rejected_rows.append(
                {"row": int(idx) + 1, "reason": "unparseable timestamp", "value": str(raw_time.loc[idx])}
            )
        notes.append(f"{int(reject_mask.sum())} row(s) had unparseable timestamps and were rejected")

    work = df.loc[~reject_mask].copy()
    parsed = parsed.loc[~reject_mask]
    if parsed.dt.tz is None:
        local = parsed.dt.tz_localize(tz, ambiguous="NaT", nonexistent="shift_forward")
    else:
        local = parsed.dt.tz_convert(tz)
    work["_ts_utc"] = local.dt.tz_convert("UTC")
    work["_local_date"] = local.dt.date

    value_roles = [r for r in cols if r not in ("timestamp", "date")]
    if not value_roles:
        raise MappingError("Map at least one value column (e.g. power or generation)")
    for role in value_roles:
        work[f"_{role}"] = _convert(work[cols[role].column], role, cols[role].unit)

    has_interval_signal = any(
        r in cols for r in ("power_w", "energy_kwh", "cumulative_energy_kwh") + tuple(INTERVAL_COLS[3:])
    )
    is_daily = time_role == "date" or (not has_interval_signal and "generation_kwh" in cols)
    if time_role == "timestamp":
        steps = work["_ts_utc"].sort_values().diff().dropna().dt.total_seconds() / 60
        steps = steps[steps > 0]
        if not steps.empty and steps.median() >= 1380 and not has_interval_signal:
            is_daily = True

    if is_daily:
        daily = pd.DataFrame({"date": work["_local_date"]})
        for role in DAILY_COLS:
            daily[role] = work[f"_{role}"] if f"_{role}" in work else np.nan
        if "_power_w" in work:
            daily["peak_power_w"] = work["_power_w"]
        else:
            daily["peak_power_w"] = np.nan
        # min_count=1 keeps all-NaN groups as NaN instead of inventing a 0 kWh day
        daily = daily.groupby("date", as_index=False).agg(
            {**{r: lambda s: s.sum(min_count=1) for r in DAILY_COLS}, "peak_power_w": "max"}
        )
        for role in DAILY_COLS:
            if f"_{role}" not in work:
                daily[role] = np.nan
        daily["reading_count"] = work.groupby("_local_date").size().reindex(daily["date"]).values
        daily["derived_from"] = "import_daily_value"
        # Rows whose generation could not be parsed are rejected explicitly, never stored as 0
        if "_generation_kwh" in work:
            bad = daily["generation_kwh"].isna()
            if bad.any():
                src_col = cols["generation_kwh"].column
                for d in daily.loc[bad, "date"]:
                    raw_vals = [str(v) for v in work.loc[work["_local_date"] == d, src_col].tolist()]
                    rejected_rows.append(
                        {
                            "row": None,
                            "reason": "missing or non-numeric generation value",
                            "value": f"{d}: {', '.join(raw_vals)[:60]}",
                        }
                    )
                notes.append(f"{int(bad.sum())} day(s) had no numeric generation value and were rejected")
                daily = daily.loc[~bad].reset_index(drop=True)
        return NormalizedData("daily", None, daily, pd.DataFrame(rejected_rows), notes)

    interval = pd.DataFrame({"ts": work["_ts_utc"], "local_date": work["_local_date"]})
    for role in INTERVAL_COLS:
        interval[role] = work[f"_{role}"] if f"_{role}" in work else np.nan
    interval = interval.sort_values("ts").reset_index(drop=True)
    dup = interval.duplicated(subset=["ts"], keep="last")
    if dup.any():
        notes.append(f"{int(dup.sum())} duplicate timestamp(s) in the file; the last occurrence was kept")
        interval = interval.loc[~dup].reset_index(drop=True)

    # Derive interval energy when missing
    if interval["energy_kwh"].isna().all():
        if interval["cumulative_energy_kwh"].notna().sum() > 1:
            diff = interval["cumulative_energy_kwh"].diff()
            diff[diff < 0] = np.nan
            interval["energy_kwh"] = diff
            notes.append("Interval energy derived from cumulative energy differences")
        elif interval["power_w"].notna().any():
            dt_h = interval["ts"].diff().dt.total_seconds().div(3600)
            med = dt_h.median()
            dt_h = dt_h.where((dt_h > 0) & (dt_h <= med * 3), med)
            interval["energy_kwh"] = interval["power_w"].fillna(0) / 1000 * dt_h.fillna(med)
            notes.append(
                f"Interval energy estimated as power x interval (median step {round(med * 60, 1)} min)"
            )

    daily = (
        interval.groupby("local_date")
        .agg(
            generation_kwh=("energy_kwh", "sum"),
            peak_power_w=("power_w", "max"),
            reading_count=("ts", "size"),
        )
        .reset_index()
        .rename(columns={"local_date": "date"})
    )
    for role in ("consumption_kwh", "exported_kwh", "imported_kwh"):
        daily[role] = np.nan
    daily["derived_from"] = "interval_aggregate"
    return NormalizedData("interval", interval, daily, pd.DataFrame(rejected_rows), notes)
