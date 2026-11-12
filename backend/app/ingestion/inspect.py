"""Column profiling and mapping suggestions for uploaded files.

Nothing here assumes specific column names: every column is profiled (type, null
ratio, parse rates, samples) and roles are *suggested* from name patterns and value
characteristics. The user confirms or overrides the mapping before any insert.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np
import pandas as pd

from app.ingestion.readers import LoadedTable

ROLES: dict[str, dict[str, Any]] = {
    "timestamp": {"label": "Timestamp (date + time)", "kind": "datetime"},
    "date": {"label": "Date (daily rows)", "kind": "date"},
    "generation_kwh": {"label": "Daily generation", "kind": "number", "units": ["kWh", "Wh", "MWh"]},
    "energy_kwh": {"label": "Interval energy", "kind": "number", "units": ["kWh", "Wh", "MWh"]},
    "cumulative_energy_kwh": {
        "label": "Cumulative / lifetime energy",
        "kind": "number",
        "units": ["kWh", "Wh", "MWh"],
    },
    "power_w": {"label": "AC power", "kind": "number", "units": ["W", "kW"]},
    "pv_voltage_v": {"label": "PV / DC voltage", "kind": "number", "units": ["V"]},
    "pv_current_a": {"label": "PV / DC current", "kind": "number", "units": ["A"]},
    "ac_voltage_v": {"label": "AC voltage", "kind": "number", "units": ["V"]},
    "ac_current_a": {"label": "AC current", "kind": "number", "units": ["A"]},
    "frequency_hz": {"label": "Grid frequency", "kind": "number", "units": ["Hz"]},
    "temperature_c": {"label": "Inverter temperature", "kind": "number", "units": ["C"]},
    "consumption_kwh": {"label": "Consumption (daily)", "kind": "number", "units": ["kWh", "Wh"]},
    "exported_kwh": {"label": "Exported to grid (daily)", "kind": "number", "units": ["kWh", "Wh"]},
    "imported_kwh": {"label": "Imported from grid (daily)", "kind": "number", "units": ["kWh", "Wh"]},
    "ignore": {"label": "Ignore", "kind": "any"},
}
ENERGY_ROLES = {
    "generation_kwh",
    "energy_kwh",
    "cumulative_energy_kwh",
    "consumption_kwh",
    "exported_kwh",
    "imported_kwh",
}

_NAME_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "timestamp",
        re.compile(
            r"(date.?time|timestamp|time.?stamp|record.?time|datetime|^time$|^ts$|collect.?time|update.?time)",
            re.I,
        ),
    ),
    ("date", re.compile(r"(^date$|^day$|\bdate\b)", re.I)),
    # Non-energy quantities that would otherwise match energy/power patterns
    (
        "ignore",
        re.compile(
            r"(operating.?hours|work.?hours|\(h\)$|hours|reactive|kvar|power.?factor|diesel|generator|charged?.?energy|discharge|battery|soc\b)",
            re.I,
        ),
    ),
    (
        "cumulative_energy_kwh",
        re.compile(r"(e.?total|total.?(yield|energy|generation|gen)|cumulative|lifetime|accumulat)", re.I),
    ),
    (
        "generation_kwh",
        re.compile(
            r"(e.?day|daily.?(yield|energy|gen)|day.?(yield|energy|gen)|energy.?generation|generation|yield|production|energy.?today|today.?energy|pv.?energy|kwh)",
            re.I,
        ),
    ),
    ("energy_kwh", re.compile(r"(interval.?energy|energy.?interval|delta.?energy)", re.I)),
    (
        "power_w",
        re.compile(r"(^pac$|^p$|active.?power|ac.?power|output.?power|^power\b|\(kw\)|\(w\)|watt)", re.I),
    ),
    ("pv_voltage_v", re.compile(r"(vpv|pv.?volt|dc.?volt|mppt.*v|string.*volt)", re.I)),
    ("pv_current_a", re.compile(r"(ipv|pv.?curr|dc.?curr|mppt.*i\b|string.*curr)", re.I)),
    ("ac_voltage_v", re.compile(r"(vac|ac.?volt|grid.?volt|phase.*volt|\bu[abc]\b)", re.I)),
    ("ac_current_a", re.compile(r"(iac|ac.?curr|grid.?curr|phase.*curr|\bi[abc]\b)", re.I)),
    ("frequency_hz", re.compile(r"(fac|freq|\bhz\b)", re.I)),
    ("temperature_c", re.compile(r"(temp|℃|°c)", re.I)),
    ("consumption_kwh", re.compile(r"(consum|load.?energy|usage)", re.I)),
    ("exported_kwh", re.compile(r"(export|feed.?in|sell|to.?grid|grid.?out)", re.I)),
    ("imported_kwh", re.compile(r"(import|purchase|buy|from.?grid|grid.?in)", re.I)),
]

_UNIT_PATTERNS = [
    ("MWh", re.compile(r"mwh", re.I)),
    ("kWh", re.compile(r"kwh|kw·h|kw h", re.I)),
    ("Wh", re.compile(r"(?<![kKmM])wh\b", re.I)),
    ("kW", re.compile(r"\bkw\b(?!h)", re.I)),
    ("W", re.compile(r"(?<![kKmM])\bw\b", re.I)),
    ("V", re.compile(r"\bv\b|volt", re.I)),
    ("A", re.compile(r"\ba\b|amp", re.I)),
    ("Hz", re.compile(r"\bhz\b", re.I)),
    ("C", re.compile(r"℃|°c|\bc\b", re.I)),
]

_ROLE_UNITS = {
    "power_w": {"W", "kW"},
    "pv_voltage_v": {"V"},
    "ac_voltage_v": {"V"},
    "pv_current_a": {"A"},
    "ac_current_a": {"A"},
    "frequency_hz": {"Hz"},
    "temperature_c": {"C"},
}


def to_numeric(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace(",", "", regex=False).str.strip()
    s = s.str.extract(r"(-?\d+(?:\.\d+)?)")[0]
    return pd.to_numeric(s, errors="coerce")


def parse_datetime(series: pd.Series, dayfirst: bool | None = None) -> pd.Series:
    """Parse strings/datetimes; returns a series aligned to the input index (NaT where unparseable)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return pd.to_datetime(series, errors="coerce")
    s = series.astype(str).str.strip().where(series.notna(), None)
    candidates: list[str | None] = [None]
    candidates += [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]
    dm = [
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y",
    ]
    md = ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y", "%m-%d-%Y %H:%M", "%m-%d-%Y"]
    candidates += (dm + md) if dayfirst in (None, True) else (md + dm)
    best: pd.Series | None = None
    best_rate = 0.0
    for fmt in candidates:
        try:
            parsed = (
                pd.to_datetime(s, errors="coerce", dayfirst=bool(dayfirst), format="mixed")
                if fmt is None
                else pd.to_datetime(s, errors="coerce", format=fmt)
            )
        except Exception:
            continue
        rate = float(parsed.notna().sum() / max(series.notna().sum(), 1))
        if rate > best_rate:
            best, best_rate = parsed, rate
        if rate >= 0.999:
            break
    if best is None:
        return pd.Series(pd.NaT, index=series.index)
    return best


def guess_unit(name: str, role: str, values: pd.Series) -> str | None:
    allowed = _ROLE_UNITS.get(role) or ({"Wh", "kWh", "MWh"} if role in ENERGY_ROLES else set())
    for unit, pat in _UNIT_PATTERNS:
        if unit in allowed and pat.search(name):
            return unit
    mx = float(values.max()) if not values.empty else float("nan")
    if role == "power_w":
        return "kW" if pd.notna(mx) and mx < 50 else "W"
    if role in ENERGY_ROLES and role != "cumulative_energy_kwh":
        return "Wh" if pd.notna(mx) and mx > 5000 else "kWh"
    if role == "cumulative_energy_kwh":
        return "kWh"
    return next(iter(sorted(allowed))) if allowed else None


def suggest_role(name: str, numeric_rate: float, dt_rate: float, dt: pd.Series) -> tuple[str | None, float]:
    if dt_rate > 0.8:
        valid = dt.dropna()
        has_time = bool(len(valid) and ((valid.dt.hour != 0) | (valid.dt.minute != 0)).mean() > 0.05)
        return ("timestamp" if has_time else "date"), 0.9
    for r, pat in _NAME_PATTERNS:
        if pat.search(name):
            if r in ("timestamp", "date"):
                continue
            if r == "ignore":
                return None, 0.0
            if ROLES[r]["kind"] == "number" and numeric_rate < 0.5:
                continue
            return r, 0.7
    return None, 0.0


def profile_table(table: LoadedTable, sample_rows: int = 5, dayfirst: bool | None = None) -> dict[str, Any]:
    df = table.df
    columns: list[dict[str, Any]] = []
    parsed_dt: dict[str, pd.Series] = {}
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        numeric = to_numeric(series)
        numeric_rate = float(numeric.notna().sum() / max(len(non_null), 1))
        looks_temporal = re.search(r"date|time|day|ts", str(col), re.I) is not None or numeric_rate < 0.9
        dt = parse_datetime(series, dayfirst) if looks_temporal else pd.Series(pd.NaT, index=series.index)
        dt_rate = float(dt.notna().sum() / max(len(non_null), 1))
        if numeric_rate > 0.95 and dt_rate > 0 and not re.search(r"date|time", str(col), re.I):
            dt_rate = 0.0  # pure numbers are not dates
        role, conf = suggest_role(str(col), numeric_rate, dt_rate, dt)
        num_vals = numeric.dropna()
        profile = {
            "name": str(col),
            "non_null": int(non_null.shape[0]),
            "null_ratio": round(float(1 - non_null.shape[0] / max(len(df), 1)), 4),
            "numeric_rate": round(numeric_rate, 3),
            "datetime_rate": round(dt_rate, 3),
            "samples": [str(v)[:40] for v in non_null.head(sample_rows).tolist()],
            "min": float(num_vals.min()) if numeric_rate > 0.5 and not num_vals.empty else None,
            "max": float(num_vals.max()) if numeric_rate > 0.5 and not num_vals.empty else None,
            "mean": round(float(num_vals.mean()), 4) if numeric_rate > 0.5 and not num_vals.empty else None,
            "suggested_role": role,
            "suggested_unit": guess_unit(str(col), role, num_vals)
            if role and ROLES[role]["kind"] == "number"
            else None,
            "confidence": conf,
        }
        if dt_rate > 0.5:
            parsed_dt[str(col)] = dt
        columns.append(profile)

    # one column per role: keep the most confident / most complete
    chosen: dict[str, dict[str, Any]] = {}
    for c in columns:
        r = c["suggested_role"]
        if not r:
            continue
        if r not in chosen:
            chosen[r] = c
        elif (c["confidence"], c["non_null"]) > (chosen[r]["confidence"], chosen[r]["non_null"]):
            chosen[r]["suggested_role"] = None
            chosen[r] = c
        else:
            c["suggested_role"] = None

    granularity = detect_granularity(parsed_dt, chosen)
    if granularity["kind"] == "interval" and "generation_kwh" in chosen and "energy_kwh" not in chosen:
        col = chosen["generation_kwh"]
        num = to_numeric(df[col["name"]]).dropna()
        if len(num) > 3 and (num.diff().dropna() >= -1e-9).mean() > 0.98:
            col["suggested_role"] = "cumulative_energy_kwh"
        else:
            col["suggested_role"] = "energy_kwh"

    head = df.head(8)
    return {
        "file_format": table.file_format,
        "layout": table.layout or "table",
        "sheet_name": table.sheet_name,
        "sheets": table.sheets,
        "header_row": table.header_row,
        "encoding": table.encoding,
        "delimiter": table.delimiter,
        "notes": table.notes,
        "meta": {k: v for k, v in table.meta.items() if k != "device"},
        "rows": int(len(df)),
        "columns": columns,
        "granularity": granularity,
        "suggested_mapping": build_suggested_mapping(columns),
        "preview": [
            {
                k: (None if (isinstance(v, float) and np.isnan(v)) else (str(v) if v is not None else None))
                for k, v in row.items()
            }
            for row in head.to_dict(orient="records")
        ],
    }


def detect_granularity(parsed_dt: dict[str, pd.Series], chosen: dict[str, dict[str, Any]]) -> dict[str, Any]:
    col = chosen.get("timestamp") or chosen.get("date")
    if not col or col["name"] not in parsed_dt:
        return {"kind": "unknown", "median_step_minutes": None, "reason": "no timestamp/date column detected"}
    dt = parsed_dt[col["name"]].dropna().sort_values()
    if len(dt) < 2:
        return {"kind": "unknown", "median_step_minutes": None, "reason": "too few timestamps"}
    steps = dt.diff().dropna().dt.total_seconds() / 60
    steps = steps[steps > 0]
    if steps.empty:
        return {"kind": "unknown", "median_step_minutes": None, "reason": "all timestamps identical"}
    med = float(steps.median())
    return {
        "kind": "daily" if med >= 1380 else "interval",
        "median_step_minutes": round(med, 1),
        "first": dt.iloc[0].isoformat(),
        "last": dt.iloc[-1].isoformat(),
        "span_days": int((dt.iloc[-1] - dt.iloc[0]).days) + 1,
    }


def build_suggested_mapping(columns: list[dict[str, Any]]) -> dict[str, Any]:
    mapping: dict[str, Any] = {"columns": {}}
    for c in columns:
        r = c["suggested_role"]
        if r:
            mapping["columns"][r] = {"column": c["name"], "unit": c["suggested_unit"]}
    return mapping
