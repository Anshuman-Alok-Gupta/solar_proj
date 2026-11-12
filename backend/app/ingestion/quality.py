"""Data quality checks for normalised import data.

Rows are never silently discarded: every rejected row is listed with a reason and
suspicious-but-kept rows carry quality flags that are stored with the data.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.ingestion.normalize import NormalizedData

MAX_DAILY_KWH_PER_KW = 9.0  # physical ceiling: ~7 kWh/kWp/day in the best climates; 9 leaves margin
MAX_POWER_FACTOR = 1.3  # AC power above 130 % of DC capacity is implausible


def _flag(df: pd.DataFrame, mask: pd.Series, flag: str) -> None:
    for idx in df.index[mask.fillna(False)]:
        df.at[idx, "quality_flags"] = (df.at[idx, "quality_flags"] or []) + [flag]


def run_quality_checks(data: NormalizedData, capacity_kw: float | None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "kind": data.kind,
        "checks": [],
        "flag_counts": {},
        "rejected": [],
        "notes": list(data.notes),
    }
    rejected: list[dict[str, Any]] = (
        data.rejected.to_dict(orient="records") if not data.rejected.empty else []
    )

    def check(name: str, status: str, detail: str, count: int = 0) -> None:
        report["checks"].append({"name": name, "status": status, "detail": detail, "count": int(count)})

    cap_w = capacity_kw * 1000 if capacity_kw else None

    if data.kind == "interval" and data.interval is not None:
        df = data.interval
        df["quality_flags"] = [None] * len(df)
        if df.empty:
            check("rows", "fail", "No usable rows after timestamp parsing")
            report["rejected"] = rejected
            return report
        # negative power
        neg = df["power_w"] < -50
        _flag(df, neg, "negative_power")
        check("negative_power", "warn" if neg.any() else "ok", "AC power below -50 W", neg.sum())
        # implausible power
        if cap_w:
            hi = df["power_w"] > cap_w * MAX_POWER_FACTOR
            _flag(df, hi, "power_above_capacity")
            check(
                "power_above_capacity",
                "warn" if hi.any() else "ok",
                f"AC power above {MAX_POWER_FACTOR:.0%} of {capacity_kw} kW capacity",
                hi.sum(),
            )
        # night-time generation (local 22:00-04:00 with > 5 % capacity)
        local_hour = (
            pd.to_datetime(df["ts"]).dt.tz_convert(None).dt.hour
        )  # UTC hour, approximation handled below
        # use local_date + ts to infer local hour precisely
        try:
            lh = pd.to_datetime(df["ts"]).dt.tz_convert(df.attrs.get("tz", "UTC")).dt.hour
        except Exception:
            lh = local_hour
        night = ((lh >= 22) | (lh <= 3)) & (df["power_w"] > (cap_w * 0.05 if cap_w else 200))
        _flag(df, night, "night_generation")
        check(
            "night_generation",
            "warn" if night.any() else "ok",
            "Generation reported between 22:00 and 04:00",
            night.sum(),
        )
        # gaps
        steps = df["ts"].diff().dt.total_seconds().div(60)
        med = float(steps.median()) if steps.notna().any() else None
        gaps = steps > (med * 3 if med else 60)
        check(
            "gaps",
            "warn" if gaps.any() else "ok",
            f"Gaps larger than 3x the median step ({med} min)",
            gaps.sum(),
        )
        # cumulative decreasing
        if df["cumulative_energy_kwh"].notna().sum() > 1:
            dec = df["cumulative_energy_kwh"].diff() < 0
            _flag(df, dec, "cumulative_decreased")
            check(
                "cumulative_monotonic",
                "warn" if dec.any() else "ok",
                "Cumulative energy decreased between rows",
                dec.sum(),
            )
        # missing values
        miss = df["power_w"].isna() & df["energy_kwh"].isna()
        _flag(df, miss, "no_value")
        check("missing_values", "warn" if miss.any() else "ok", "Rows without power or energy", miss.sum())
        report["summary"] = {
            "rows": int(len(df)),
            "first_ts": df["ts"].min().isoformat(),
            "last_ts": df["ts"].max().isoformat(),
            "median_step_minutes": med,
            "days": int(df["local_date"].nunique()),
            "total_energy_kwh": round(float(df["energy_kwh"].sum(skipna=True)), 3),
            "peak_power_w": float(df["power_w"].max()) if df["power_w"].notna().any() else None,
        }

    if data.daily is not None:
        dd = data.daily
        if "quality_flags" not in dd:
            dd["quality_flags"] = [None] * len(dd)
        neg = dd["generation_kwh"] < 0
        _flag(dd, neg, "negative_generation")
        check("negative_generation", "warn" if neg.any() else "ok", "Daily generation below zero", neg.sum())
        if capacity_kw:
            hi = dd["generation_kwh"] > capacity_kw * MAX_DAILY_KWH_PER_KW
            _flag(dd, hi, "daily_above_physical_limit")
            check(
                "daily_physical_limit",
                "warn" if hi.any() else "ok",
                f"Daily generation above {MAX_DAILY_KWH_PER_KW} kWh per kW",
                hi.sum(),
            )
        zero = dd["generation_kwh"] == 0
        _flag(dd, zero, "zero_generation_day")
        check(
            "zero_days",
            "warn" if zero.any() else "ok",
            "Days with exactly 0 kWh (outage, missing data or genuine)",
            zero.sum(),
        )
        dupd = dd.duplicated(subset=["date"])
        check("duplicate_dates", "warn" if dupd.any() else "ok", "Duplicate dates in the file", dupd.sum())
        if data.kind == "daily":
            dates = pd.to_datetime(dd["date"]).sort_values()
            missing_days = int(((dates.diff().dt.days - 1).clip(lower=0)).sum()) if len(dates) > 1 else 0
            check(
                "missing_days",
                "warn" if missing_days else "ok",
                "Calendar days absent between first and last date",
                missing_days,
            )
        report["daily_summary"] = {
            "days": int(len(dd)),
            "first_date": str(dd["date"].min()),
            "last_date": str(dd["date"].max()),
            "total_kwh": round(float(dd["generation_kwh"].sum(skipna=True)), 3),
            "mean_kwh": round(float(dd["generation_kwh"].mean()), 3) if len(dd) else None,
            "max_kwh": round(float(dd["generation_kwh"].max()), 3) if len(dd) else None,
        }

    counts: dict[str, int] = {}
    for frame in (data.interval, data.daily):
        if frame is not None and "quality_flags" in frame:
            for flags in frame["quality_flags"]:
                for f in flags or []:
                    counts[f] = counts.get(f, 0) + 1
    report["flag_counts"] = counts
    report["rejected"] = rejected
    report["rejected_count"] = len(rejected)
    report["status"] = (
        "fail"
        if any(c["status"] == "fail" for c in report["checks"])
        else ("warn" if any(c["status"] == "warn" for c in report["checks"]) else "ok")
    )
    return report


def clean_nan(obj: Any) -> Any:
    """Make a report JSON-safe (NaN -> None, numpy -> python)."""
    if isinstance(obj, dict):
        return {k: clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_nan(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj
