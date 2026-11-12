"""Unpivot SEMS "wide" report exports into long tables.

Verified against real exports on 2026-11-11:

Inverter Operation Report (one day, 5-minute slots)
    row k: "Report Date:11/11/2026"
    header: Device Information | Station Information | Indicator | 00:00 | 00:05 | ... | 23:55
    rows:   <device> | <station> | Active Power(kW) | v | v | ...
            ... Chamber Temperature(°C), Cumulative Operating Hours(H), Reactive Power(kVar), Power Factor ...

Inverter Statistical Report (date range, daily)
    row k: "Report Cycle:01/11/2026 ~ 11/11/2026"
    header: Device Information | Indicator | Total Value | 01/11/2026 | 02/11/2026 | ...
    rows:   <device> | Energy Generation(kWh) | total | v | v | ...

Both become long tables with one row per timestamp/date and one column per indicator,
so the generic column-mapping pipeline handles them like any other file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_REPORT_DATE_RE = re.compile(r"Report\s*Date\s*[:：]\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
_REPORT_CYCLE_RE = re.compile(r"Report\s*Cycle\s*[:：]", re.I)


@dataclass(slots=True)
class WideReport:
    kind: str  # "operation" | "statistical"
    df: pd.DataFrame
    meta: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _cell(v) -> str:
    return "" if v is None else str(v).strip()


def detect_wide_report(grid: list[list]) -> tuple[str, int] | None:
    """Return (kind, header_row_index) if the grid looks like a SEMS wide report."""
    for i, row in enumerate(grid[:15]):
        cells = [_cell(c) for c in row]
        if "Indicator" in cells:
            after = cells[cells.index("Indicator") + 1 :]
            if sum(1 for c in after if _TIME_RE.match(c)) >= 10:
                return "operation", i
            if sum(1 for c in after if _DATE_RE.match(c)) >= 1:
                return "statistical", i
    return None


def _parse_device_meta(text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in str(text).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip().lower().replace(" ", "_")
            if key in ("inverter_name", "inverter_sn", "station_name"):
                meta[key] = v.strip()
    return meta


def unpivot_wide_report(grid: list[list], kind: str, header_row: int) -> WideReport:
    header = [_cell(c) for c in grid[header_row]]
    ind_idx = header.index("Indicator")
    notes: list[str] = []
    meta: dict[str, Any] = {"layout": f"sems_wide_{kind}_report"}

    report_date: datetime | None = None
    for row in grid[:header_row]:
        for c in row:
            m = _REPORT_DATE_RE.search(_cell(c))
            if m:
                report_date = datetime.strptime(m.group(1), "%d/%m/%Y")
            if _REPORT_CYCLE_RE.search(_cell(c)):
                meta["report_cycle"] = _cell(c).split(":", 1)[-1].strip()
    if kind == "operation":
        if report_date is None:
            raise ValueError("Operation report: 'Report Date:' not found above the header row")
        meta["report_date"] = report_date.date().isoformat()

    value_cols = [
        (j, h) for j, h in enumerate(header) if j > ind_idx and (_TIME_RE.match(h) or _DATE_RE.match(h))
    ]
    if not value_cols:
        raise ValueError("No time/date columns found in the wide report header")

    series: dict[str, dict[str, Any]] = {}
    devices: set[str] = set()
    for row in grid[header_row + 1 :]:
        cells = list(row) + [None] * (len(header) - len(row))
        indicator = _cell(cells[ind_idx])
        if not indicator:
            continue
        dev_meta = _parse_device_meta(_cell(cells[0]))
        if dev_meta:
            devices.add(dev_meta.get("inverter_sn") or dev_meta.get("inverter_name") or "device")
            meta.setdefault("device", dev_meta)
        col = series.setdefault(indicator, {})
        for j, h in value_cols:
            raw = cells[j]
            if raw is None or _cell(raw) == "":
                continue
            if kind == "operation":
                key = f"{report_date.date().isoformat()} {h}"
            else:
                key = datetime.strptime(h, "%d/%m/%Y").date().isoformat()
            col[key] = raw
    if len(devices) > 1:
        notes.append(
            f"Report contains {len(devices)} devices; values were combined per indicator (last wins)"
        )

    index_name = "timestamp" if kind == "operation" else "date"
    keys = sorted({k for col in series.values() for k in col})
    data = {index_name: keys}
    for indicator, col in series.items():
        data[indicator] = [col.get(k) for k in keys]
    df = pd.DataFrame(data)
    notes.append(
        f"Unpivoted SEMS {kind} report: {len(series)} indicator(s) x {len(keys)} "
        + ("time slots" if kind == "operation" else "days")
    )
    if kind == "operation":
        notes.append("Dates in the report are day-first (dd/mm/yyyy); timestamps are station local time")
    return WideReport(kind=kind, df=df, meta=meta, notes=notes)
