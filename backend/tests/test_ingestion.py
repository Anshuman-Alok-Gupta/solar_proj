"""Ingestion pipeline tests: readers, wide-report unpivot, mapping suggestions, normalisation, quality."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.ingestion.inspect import profile_table
from app.ingestion.normalize import ColumnMapping, MappingError, normalize
from app.ingestion.quality import clean_nan, run_quality_checks
from app.ingestion.readers import load_table


def _write_csv(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_daily_csv_with_title_rows(tmp_path):
    p = _write_csv(
        tmp_path,
        "daily.csv",
        "My Solar Export\nStation: X\n\nDate,Energy Generation(kWh),Income\n2026-10-01,14.2,0\n2026-10-02,13.9,0\n2026-10-03,bad,0\n2026-10-04,,0\n",
    )
    t = load_table(p)
    assert t.header_row == 3 and list(t.df.columns)[:2] == ["Date", "Energy Generation(kWh)"]
    prof = profile_table(t)
    m = prof["suggested_mapping"]["columns"]
    assert m["date"]["column"] == "Date"
    assert m["generation_kwh"]["column"] == "Energy Generation(kWh)" and m["generation_kwh"]["unit"] == "kWh"
    assert prof["granularity"]["kind"] == "daily"
    data = normalize(t.df, ColumnMapping.model_validate(prof["suggested_mapping"]), "Asia/Kolkata")
    assert data.kind == "daily"
    # 'bad' and the empty cell cannot be parsed: they are rejected with a reason, never stored as 0 kWh
    assert len(data.daily) == 2 and len(data.rejected) == 2
    assert list(data.daily["generation_kwh"]) == [14.2, 13.9]
    rep = clean_nan(run_quality_checks(data, 5.0))
    assert rep["status"] in ("ok", "warn")


def test_interval_csv_power_only_derives_energy_and_daily(tmp_path):
    rows = ["Timestamp,Power (W)"]
    for h in range(6, 19):
        for m in (0, 30):
            rows.append(f"2026-10-01 {h:02d}:{m:02d},{max(0, 4000 - abs(12 - h - m / 60) * 600):.0f}")
    p = _write_csv(tmp_path, "interval.csv", "\n".join(rows) + "\n")
    t = load_table(p)
    prof = profile_table(t)
    assert prof["granularity"]["kind"] == "interval" and prof["granularity"]["median_step_minutes"] == 30
    mapping = ColumnMapping.model_validate(prof["suggested_mapping"])
    assert mapping.columns["power_w"].unit == "W"
    data = normalize(t.df, mapping, "Asia/Kolkata")
    assert data.kind == "interval"
    assert data.interval["energy_kwh"].notna().any()
    assert len(data.daily) == 1 and 15 < data.daily["generation_kwh"].iloc[0] < 40
    # timestamps were naive -> interpreted in plant tz -> stored UTC (06:00 IST == 00:30 UTC)
    assert data.interval["ts"].iloc[0].strftime("%H:%M") == "00:30"


def test_cumulative_column_detected_and_diffed(tmp_path):
    p = _write_csv(
        tmp_path,
        "cum.csv",
        "time,E-Total(kWh)\n2026-10-01 08:00,100.0\n2026-10-01 09:00,100.8\n2026-10-01 10:00,101.9\n2026-10-01 11:00,103.0\n2026-10-01 12:00,104.5\n",
    )
    t = load_table(p)
    prof = profile_table(t)
    assert prof["suggested_mapping"]["columns"]["cumulative_energy_kwh"]["column"] == "E-Total(kWh)"
    data = normalize(t.df, ColumnMapping.model_validate(prof["suggested_mapping"]), "UTC")
    assert data.kind == "interval"
    assert abs(data.daily["generation_kwh"].iloc[0] - 4.5) < 1e-6


def test_operating_hours_not_mapped_as_energy(tmp_path):
    p = _write_csv(
        tmp_path,
        "hours.csv",
        "timestamp,Cumulative Operating Hours(H),Active Power(kW)\n2026-10-01 08:00,800,1.2\n2026-10-01 08:05,800,1.3\n2026-10-01 08:10,800,1.4\n",
    )
    prof = profile_table(load_table(p))
    roles = {c["name"]: c["suggested_role"] for c in prof["columns"]}
    assert roles["Cumulative Operating Hours(H)"] is None
    assert roles["Active Power(kW)"] == "power_w"


def test_wide_operation_report_unpivot(tmp_path):
    times = [f"{h:02d}:{m:02d}" for h in range(24) for m in range(0, 60, 5)]
    header = ["Device Information", "Station Information", "Indicator", *times]
    power = [
        "Inverter Name:Inv 1\nInverter SN:ABC",
        "Station Name:S",
        "Active Power(kW)",
        *["" for _ in times],
    ]
    for i, t in enumerate(times):
        h = int(t[:2])
        if 7 <= h <= 17:
            power[3 + i] = f"{max(0.0, 2.5 - abs(12 - h) * 0.3):.2f}"
    temp = [
        "Inverter Name:Inv 1\nInverter SN:ABC",
        "Station Name:S",
        "Chamber Temperature(°C)",
        *["35" if 7 <= int(t[:2]) <= 17 else "" for t in times],
    ]
    df = pd.DataFrame(
        [["" for _ in header], ["Report Date:11/11/2026", *["" for _ in header[1:]]], header, power, temp]
    )
    path = tmp_path / "op.xlsx"
    df.to_excel(path, header=False, index=False)
    t = load_table(path)
    assert t.layout == "sems_wide_operation_report"
    assert set(t.df.columns) >= {"timestamp", "Active Power(kW)", "Chamber Temperature(°C)"}
    prof = profile_table(t)
    m = prof["suggested_mapping"]["columns"]
    assert m["power_w"]["column"] == "Active Power(kW)" and m["power_w"]["unit"] == "kW"
    assert m["temperature_c"]["column"] == "Chamber Temperature(°C)"
    data = normalize(t.df, ColumnMapping.model_validate(prof["suggested_mapping"]), "Asia/Kolkata")
    assert data.kind == "interval" and str(data.daily["date"].iloc[0]) == "2026-11-11"
    assert data.daily["generation_kwh"].iloc[0] > 5


def test_wide_statistical_report_unpivot(tmp_path):
    header = ["Device Information", "Indicator", "Total Value", "01/11/2026", "02/11/2026", "03/11/2026"]
    rows = [
        ["", "", "", "", "", ""],
        ["Report Cycle:01/11/2026 ~ 03/11/2026", "", "", "", "", ""],
        header,
        ["Inverter SN:ABC\nStation Name:S", "Energy Generation(kWh)", "33.2", "13.0", "4.8", "15.4"],
        ["Inverter SN:ABC\nStation Name:S", "Charged Energy(kWh)", "0", "0", "0", "0"],
    ]
    path = tmp_path / "stat.xlsx"
    pd.DataFrame(rows).to_excel(path, header=False, index=False)
    t = load_table(path)
    assert t.layout == "sems_wide_statistical_report"
    prof = profile_table(t)
    m = prof["suggested_mapping"]["columns"]
    assert m["date"]["column"] == "date" and m["generation_kwh"]["column"] == "Energy Generation(kWh)"
    assert "Charged Energy(kWh)" not in {v["column"] for v in m.values()}
    data = normalize(t.df, ColumnMapping.model_validate(prof["suggested_mapping"]), "Asia/Kolkata")
    assert data.kind == "daily" and list(data.daily["generation_kwh"]) == [13.0, 4.8, 15.4]


def test_mapping_requires_time_and_value(tmp_path):
    p = _write_csv(tmp_path, "x.csv", "a,b\n1,2\n")
    t = load_table(p)
    with pytest.raises(MappingError):
        normalize(t.df, ColumnMapping(columns={}), "UTC")
    with pytest.raises(MappingError):
        normalize(t.df, ColumnMapping.model_validate({"columns": {"timestamp": {"column": "a"}}}), "UTC")


def test_quality_flags_implausible_values(tmp_path):
    p = _write_csv(tmp_path, "q.csv", "date,kwh\n2026-10-01,14\n2026-10-02,-2\n2026-10-03,99\n2026-10-04,0\n")
    t = load_table(p)
    prof = profile_table(t)
    data = normalize(t.df, ColumnMapping.model_validate(prof["suggested_mapping"]), "UTC")
    rep = clean_nan(run_quality_checks(data, 5.0))
    assert rep["flag_counts"].get("negative_generation") == 1
    assert rep["flag_counts"].get("daily_above_physical_limit") == 1
    assert rep["flag_counts"].get("zero_generation_day") == 1
    assert rep["status"] == "warn"
