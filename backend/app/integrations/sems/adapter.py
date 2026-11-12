"""Normalise raw SEMS+ responses into platform rows.

Factor codes verified against the real account on 2026-11-11 (GW6000-SDT-20):
  telemetry:  pAc (kW), qAc, gridPF, PHASE-{A,B,C}:{Pac,Vac,Iac,fac}, Fac (Hz),
              MPPT-{1,2}:{Ppv (kW),Vpv (V),Ipv (A)}, Temperature (C), hTotal (h), ISOLimit, LeakageCurrent
  counters:   pAc, ratedPower (kW), proPvStatsToday/Week/Month/Year/Total (kWh)
  statistics: dataList[{item, unit, statisticsList[{date, val}]}] with items
              proSystemTotalStats (generation), proConsumStats, proGridStats (export),
              proPurchaseStats (import), proSelfConsumStats, proCharStats, proDischarStats
  flow:       pAc, consumFlag (False => no smart meter: consumption is NOT measured), refreshTime (station local)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.db.models import DataSource


def flatten_factors(groups: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g in groups or []:
        if not isinstance(g, dict):
            continue
        for f in g.get("factors", []) or []:
            if isinstance(f, dict) and isinstance(f.get("code"), str) and f.get("data") is not None:
                out[f["code"]] = f["data"]
    return out


def _num(factors: dict[str, Any], *codes: str) -> float | None:
    for c in codes:
        v = factors.get(c)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _sum(factors: dict[str, Any], codes: list[str]) -> float | None:
    vals = [v for v in (_num(factors, c) for c in codes) if v is not None]
    return sum(vals) if vals else None


def _mean(factors: dict[str, Any], codes: list[str]) -> float | None:
    vals = [v for v in (_num(factors, c) for c in codes) if v is not None]
    return sum(vals) / len(vals) if vals else None


def parse_station_local_time(value: str | None, tz_name: str) -> datetime | None:
    if not value:
        return None
    try:
        naive = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(UTC)
    return naive.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


def build_live_reading(
    *,
    plant_id: int,
    inverter_id: int | None,
    telemetry: Any,
    counters: Any,
    flow: dict[str, Any],
    device_status: Any,
    tz_name: str,
    sync_log_id: int | None,
    previous_cumulative_kwh: float | None = None,
) -> dict[str, Any]:
    tf = flatten_factors(telemetry)
    cf = flatten_factors(counters)
    ts = parse_station_local_time(flow.get("refreshTime"), tz_name) or datetime.now(UTC)
    ts = ts.replace(second=0, microsecond=0)

    power_kw = _num(tf, "pAc")
    if power_kw is None:
        power_kw = _num(cf, "pAc")
    if power_kw is None and isinstance(flow.get("pAc"), (int, float)):
        power_kw = float(flow["pAc"])
    flags: list[str] = []
    if power_kw is None and device_status in (-1, 0, "-1", "0"):
        # Inverter reports nothing in standby/offline (e.g. at night); HA convention: 0 W.
        power_kw = 0.0
        flags.append("power_assumed_zero_standby")
    cumulative = _num(cf, "proPvStatsTotal")
    energy_kwh = None
    if cumulative is not None and previous_cumulative_kwh is not None:
        delta = cumulative - previous_cumulative_kwh
        if 0 <= delta <= 50:
            energy_kwh = delta
        elif delta < 0:
            flags.append("cumulative_decreased")
    return {
        "plant_id": plant_id,
        "inverter_id": inverter_id,
        "ts": ts,
        "power_w": power_kw * 1000 if power_kw is not None else None,
        "energy_kwh": energy_kwh,
        "cumulative_energy_kwh": cumulative,
        "pv_voltage_v": _mean(tf, ["MPPT-1:Vpv", "MPPT-2:Vpv", "MPPT-3:Vpv", "MPPT-4:Vpv"]),
        "pv_current_a": _sum(tf, ["MPPT-1:Ipv", "MPPT-2:Ipv", "MPPT-3:Ipv", "MPPT-4:Ipv"]),
        "ac_voltage_v": _num(tf, "PHASE-A:Vac", "Vac"),
        "ac_current_a": _sum(tf, ["PHASE-A:Iac", "PHASE-B:Iac", "PHASE-C:Iac"]) or _num(tf, "Iac"),
        "frequency_hz": _num(tf, "Fac", "PHASE-A:fac"),
        "temperature_c": _num(tf, "Temperature"),
        "source": DataSource.SEMS_API,
        "quality_flags": flags or None,
        "sync_log_id": sync_log_id,
        "_extra": {
            "eday_kwh": _num(cf, "proPvStatsToday"),
            "rated_power_kw": _num(cf, "ratedPower"),
            "mppt": {k: v for k, v in tf.items() if k.startswith("MPPT")},
            "phases": {k: v for k, v in tf.items() if k.startswith("PHASE")},
            "hours_total": _num(tf, "hTotal"),
        },
    }


STAT_ITEM_MAP = {
    "proSystemTotalStats": "generation_kwh",
    "proConsumStats": "consumption_kwh",
    "proGridStats": "exported_kwh",
    "proPurchaseStats": "imported_kwh",
    "proSelfConsumStats": "self_consumed_kwh",
}


def _looks_unmetered(vals: dict[str, Any]) -> bool:
    """SEMS's placeholder pattern without a meter: no grid exchange and consumption == production."""
    if "exported_kwh" not in vals or "imported_kwh" not in vals:
        return False  # grid exchange not reported at all: nothing to judge by
    gen = vals.get("generation_kwh") or 0.0
    exp, imp = vals.get("exported_kwh") or 0.0, vals.get("imported_kwh") or 0.0
    cons = vals.get("consumption_kwh")
    return gen > 0 and exp == 0 and imp == 0 and (cons is None or abs(cons - gen) < 1e-6)


def statistics_to_daily_rows(
    stats: dict[str, Any],
    *,
    plant_id: int,
    today_local: date,
    has_meter: bool,
    sync_log_id: int | None,
) -> list[dict[str, Any]]:
    """Convert a `dimension=day` statistics response into DailyEnergy rows.

    Without a smart meter (consumFlag False) SEMS fills consumption / self-consumption
    with the production value. Those are assumptions, not measurements, so they are
    stored as NULL and the gap is documented in quality_flags.
    """
    by_date: dict[date, dict[str, Any]] = {}
    for item in stats.get("dataList", []) or []:
        if not isinstance(item, dict):
            continue
        col = STAT_ITEM_MAP.get(item.get("item"))
        if col is None:
            continue
        for s in item.get("statisticsList", []) or []:
            if not isinstance(s, dict):
                continue
            try:
                d = date.fromisoformat(str(s.get("date"))[:10])
                val = float(s.get("val")) if s.get("val") is not None else None
            except (TypeError, ValueError):
                continue
            by_date.setdefault(d, {})[col] = val

    rows: list[dict[str, Any]] = []
    for d, vals in sorted(by_date.items()):
        gen = vals.get("generation_kwh")
        if gen is None:
            continue
        flags: list[str] = []
        if d >= today_local:
            flags.append("partial_day")
        metered = has_meter and not _looks_unmetered(vals)
        if not metered:
            flags.append("no_smart_meter_consumption_unmeasured")
        rows.append(
            {
                "plant_id": plant_id,
                "date": d,
                "generation_kwh": gen,
                "consumption_kwh": vals.get("consumption_kwh") if metered else None,
                "exported_kwh": vals.get("exported_kwh") if metered else None,
                "imported_kwh": vals.get("imported_kwh") if metered else None,
                "self_consumed_kwh": vals.get("self_consumed_kwh") if metered else None,
                "source": DataSource.SEMS_API,
                "derived_from": "sems_daily_statistics",
                "is_complete": d < today_local,
                "quality_flags": flags or None,
                "sync_log_id": sync_log_id,
            }
        )
    return rows
