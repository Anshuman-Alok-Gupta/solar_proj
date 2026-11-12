"""SEMS synchronisation service: inverter discovery, daily history, live snapshots.

Modes
-----
* full        : daily statistics from the plant installation date (or station creation) to today
* incremental : daily statistics for the last N days (SEMS revises recent days) + live snapshot
* snapshot    : live telemetry only (one EnergyReading row per inverter)
* curves      : backfill 5-minute power curves for past days (no daily statistics)

Power curves: incremental runs refresh today's curve and backfill a few missing past days per
run (polite: one request per day, spaced out), so the full history fills in automatically.
Every run is recorded in ``sync_logs`` with counts, range, message and error.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, desc, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DailyEnergy, DataSource, EnergyReading, Inverter, Plant, SyncLog, SyncStatus
from app.integrations.sems.adapter import (
    CURVE_FLAG,
    build_live_reading,
    curve_to_readings,
    parse_station_local_time,
    statistics_to_daily_rows,
)
from app.integrations.sems.client import SemsClient, SemsError
from app.services.energy_store import upsert_daily_energy, upsert_energy_readings

log = logging.getLogger(__name__)

INCREMENTAL_LOOKBACK_DAYS = 7
CURVE_MIN_READINGS = 60  # a day with fewer curve-derived readings than this is re-fetched
CURVE_BACKFILL = {"incremental": 4, "full": 31, "curves": 62}
CURVE_REQUEST_SPACING_S = 4.0
STATS_CHUNK_DAYS = 180


class SemsNotConfigured(RuntimeError):
    pass


def make_client() -> SemsClient:
    s = get_settings()
    if not s.sems_configured:
        raise SemsNotConfigured("SEMS_ACCOUNT / SEMS_PASSWORD are not configured")
    return SemsClient(s.sems_account, s.sems_password)


def resolve_station_id(client: SemsClient, plant: Plant) -> tuple[str, dict[str, Any]]:
    """Use the configured station id or auto-discover (single-station accounts)."""
    s = get_settings()
    stations = client.list_stations()
    wanted = plant.sems_station_id or s.sems_station_id
    if wanted:
        for st in stations:
            if st.get("id") == wanted:
                return wanted, st
        return wanted, {}
    if len(stations) == 1:
        return stations[0]["id"], stations[0]
    if not stations:
        raise SemsError("No stations found on this SEMS account")
    raise SemsError(
        f"Account has {len(stations)} stations; set SEMS_STATION_ID or plant.sems_station_id to one of: "
        + ", ".join(str(st.get("id")) for st in stations)
    )


def _plant_today(plant: Plant) -> date:
    return datetime.now(ZoneInfo(plant.timezone or "UTC")).date()


def ensure_inverters(db: Session, plant: Plant, devices: list[dict[str, Any]]) -> dict[str, Inverter]:
    by_sn: dict[str, Inverter] = {}
    for d in devices:
        sn = d.get("sn")
        if not sn or d.get("deviceType") not in ("INVERTER", "ENERGY_STORAGE_INTEGRATED_CABINET"):
            continue
        inv = db.scalar(select(Inverter).where(Inverter.plant_id == plant.id, Inverter.serial_number == sn))
        if inv is None:
            inv = Inverter(
                plant_id=plant.id, serial_number=sn, source=DataSource.SEMS_API, manufacturer="GoodWe"
            )
            db.add(inv)
        inv.name = d.get("name") or inv.name
        inv.model = d.get("model") or inv.model
        inv.extra = {k: v for k, v in d.items() if k != "sn"}
        by_sn[sn] = inv
    db.flush()
    return by_sn


def sync_daily_statistics(
    db: Session,
    client: SemsClient,
    plant: Plant,
    station_id: str,
    start: date,
    end: date,
    has_meter: bool,
    log_id: int,
) -> dict[str, int]:
    totals = {"fetched": 0, "inserted": 0, "updated": 0, "skipped": 0}
    cursor = start
    today = _plant_today(plant)
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=STATS_CHUNK_DAYS - 1), end)
        stats = client.station_statistics(
            station_id,
            "day",
            datetime.combine(cursor, datetime.min.time()),
            datetime.combine(chunk_end, datetime.max.time()).replace(microsecond=0),
        )
        rows = statistics_to_daily_rows(
            stats, plant_id=plant.id, today_local=today, has_meter=has_meter, sync_log_id=log_id
        )
        totals["fetched"] += len(rows)
        res = upsert_daily_energy(db, rows)
        totals["inserted"] += res["inserted"]
        totals["updated"] += res["updated"]
        totals["skipped"] += res["skipped"]
        cursor = chunk_end + timedelta(days=1)
    return totals


def _days_needing_curves(db: Session, plant: Plant, today: date, limit: int) -> list[date]:
    """Today first, then past SEMS days (newest first) without a usable intraday curve."""
    tz = ZoneInfo(plant.timezone)
    first = db.scalar(
        select(func.min(DailyEnergy.date)).where(
            DailyEnergy.plant_id == plant.id, DailyEnergy.source == DataSource.SEMS_API
        )
    )
    if first is None:
        return [today]
    start_utc = datetime.combine(first, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    rows = db.execute(
        select(EnergyReading.ts).where(
            EnergyReading.plant_id == plant.id,
            EnergyReading.ts >= start_utc,
            cast(EnergyReading.quality_flags, String).like(f"%{CURVE_FLAG}%"),
        )
    ).all()
    per_day: dict[date, int] = {}
    for (ts,) in rows:
        d = ts.astimezone(tz).date()
        per_day[d] = per_day.get(d, 0) + 1
    out = [today]
    d = today - timedelta(days=1)
    while d >= first and len(out) < limit + 1:
        if per_day.get(d, 0) < CURVE_MIN_READINGS:
            out.append(d)
        d -= timedelta(days=1)
    return out


def sync_power_curves(
    db: Session, client: SemsClient, plant: Plant, station_id: str, today: date, log_id: int, max_days: int
) -> dict[str, Any]:
    import time

    tz = ZoneInfo(plant.timezone)
    inverters = list(db.scalars(select(Inverter).where(Inverter.plant_id == plant.id)))
    inverter_id = inverters[0].id if len(inverters) == 1 else None  # station curve == the inverter
    days = _days_needing_curves(db, plant, today, max_days)
    totals: dict[str, Any] = {"days": 0, "readings": 0, "empty_days": [], "remaining_estimate": None}
    for i, d in enumerate(days):
        if i:
            time.sleep(CURVE_REQUEST_SPACING_S)
        noon = datetime.combine(d, datetime.min.time(), tzinfo=tz) + timedelta(hours=12)
        offset_h = (noon.utcoffset() or timedelta()).total_seconds() / 3600
        try:
            points = client.power_curve(station_id, d, offset_h)
        except SemsError as exc:
            if i == 0:
                raise
            # keep what was stored; the next scheduled run continues the backfill
            totals["stopped"] = f"{exc} (continues on the next run)"
            break
        rows = curve_to_readings(
            points, plant_id=plant.id, inverter_id=inverter_id, tz_name=plant.timezone, sync_log_id=log_id
        )
        if not rows:
            totals["empty_days"].append(d.isoformat())
            continue
        upsert_energy_readings(db, rows)
        db.commit()
        totals["days"] += 1
        totals["readings"] += len(rows)
    return totals


def take_snapshot(
    db: Session, client: SemsClient, plant: Plant, station_id: str, devices: list[dict[str, Any]], log_id: int
) -> list[dict[str, Any]]:
    flow = client.station_flow(station_id)
    inverters = ensure_inverters(db, plant, devices)
    readings: list[dict[str, Any]] = []
    for d in devices:
        sn = d.get("sn")
        if not sn or sn not in inverters:
            continue
        dtype = d.get("deviceType", "INVERTER")
        telemetry = client.device_telemetry(station_id, sn, dtype)
        counters = client.device_telecounting(station_id, sn, dtype)
        inv = inverters[sn]
        prev = db.scalar(
            select(EnergyReading.cumulative_energy_kwh)
            .where(
                EnergyReading.plant_id == plant.id,
                EnergyReading.inverter_id == inv.id,
                EnergyReading.cumulative_energy_kwh.is_not(None),
            )
            .order_by(desc(EnergyReading.ts))
            .limit(1)
        )
        row = build_live_reading(
            plant_id=plant.id,
            inverter_id=inv.id,
            telemetry=telemetry,
            counters=counters,
            flow=flow,
            device_status=d.get("status"),
            tz_name=plant.timezone,
            sync_log_id=log_id,
            previous_cumulative_kwh=prev,
        )
        extra = row["_extra"]
        if extra.get("rated_power_kw") and not inv.rated_power_kw:
            inv.rated_power_kw = extra["rated_power_kw"]
        readings.append(row)
    if readings:
        upsert_energy_readings(db, [{k: v for k, v in r.items() if k != "_extra"} for r in readings])
    return readings


def run_sems_sync(
    db: Session, plant: Plant, *, mode: str = "incremental", client: SemsClient | None = None
) -> SyncLog:
    log_row = SyncLog(plant_id=plant.id, source="sems", mode=mode, status=SyncStatus.RUNNING)
    db.add(log_row)
    db.commit()
    own = client is None
    details: dict[str, Any] = {}
    today = _plant_today(plant)
    try:
        client = client or make_client()
        station_id, station = resolve_station_id(client, plant)
        if plant.sems_station_id != station_id:
            plant.sems_station_id = station_id
        details["station"] = {
            "name": station.get("name"),
            "installed_power_kw": station.get("installedPower"),
            "production_total_kwh": station.get("productionTotal"),
            "production_today_kwh": station.get("productionToday"),
            "create_time": station.get("createTime"),
            "time_zone": station.get("timeZone"),
        }
        devices = client.station_devices(station_id)
        details["devices"] = [
            {
                "sn": f"...{str(d.get('sn'))[-4:]}",
                "model": d.get("model"),
                "type": d.get("deviceType"),
                "status": d.get("status"),
            }
            for d in devices
        ]
        flow = client.station_flow(station_id)
        # "flows" is present even without a meter; only consumFlag means consumption is metered
        has_meter = bool(flow.get("consumFlag"))
        details["has_smart_meter"] = has_meter
        details["station_refresh_time"] = flow.get("refreshTime")
        ensure_inverters(db, plant, devices)

        if mode in ("full", "incremental"):
            if mode == "full":
                start = plant.installation_date
                if start is None and station.get("createTime"):
                    start = date.fromisoformat(str(station["createTime"])[:10])
                start = start or (today - timedelta(days=400))
            else:
                last = db.scalar(
                    select(func.max(DailyEnergy.date)).where(
                        DailyEnergy.plant_id == plant.id, DailyEnergy.source == DataSource.SEMS_API
                    )
                )
                start = (
                    (last - timedelta(days=INCREMENTAL_LOOKBACK_DAYS))
                    if last
                    else (today - timedelta(days=60))
                )
            daily = sync_daily_statistics(db, client, plant, station_id, start, today, has_meter, log_row.id)
            details["daily"] = daily
            log_row.range_start = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
            log_row.range_end = datetime.combine(today, datetime.min.time(), tzinfo=UTC)
            log_row.records_fetched += daily["fetched"]
            log_row.records_inserted += daily["inserted"]
            log_row.records_updated += daily["updated"]

        if mode in CURVE_BACKFILL:
            curves = sync_power_curves(db, client, plant, station_id, today, log_row.id, CURVE_BACKFILL[mode])
            details["curves"] = curves
            log_row.records_fetched += curves["readings"]

        if mode in ("incremental", "snapshot"):
            readings = take_snapshot(db, client, plant, station_id, devices, log_row.id)
            details["snapshot"] = [
                {
                    "inverter_id": r["inverter_id"],
                    "ts": r["ts"].isoformat(),
                    "power_w": r["power_w"],
                    "eday_kwh": r["_extra"].get("eday_kwh"),
                    "etotal_kwh": r["cumulative_energy_kwh"],
                    "temperature_c": r["temperature_c"],
                    "flags": r["quality_flags"],
                }
                for r in readings
            ]
            log_row.records_fetched += len(readings)
            log_row.records_inserted += len(readings)
            refresh = parse_station_local_time(flow.get("refreshTime"), plant.timezone)
            if refresh and datetime.now(UTC) - refresh > timedelta(hours=2):
                details["warning"] = f"Station data is stale: last portal refresh {refresh.isoformat()}"

        log_row.status = SyncStatus.SUCCESS
        parts = []
        if "daily" in details:
            parts.append(f"{details['daily']['fetched']} daily rows ({details['daily']['inserted']} new)")
        if "curves" in details:
            parts.append(f"power curves for {details['curves']['days']} day(s)")
        if "snapshot" in details:
            parts.append(f"{len(details['snapshot'])} live reading(s)")
        log_row.message = ", ".join(parts) or "nothing to do"
        db.commit()
    except (SemsError, SemsNotConfigured) as exc:
        db.rollback()
        log_row.status = SyncStatus.FAILED
        log_row.error = str(exc)
        if getattr(exc, "retry_after", None):
            details["retry_after_seconds"] = exc.retry_after
        log.warning("SEMS sync failed for plant %s: %s", plant.id, exc)
    except Exception as exc:  # pragma: no cover
        db.rollback()
        log_row.status = SyncStatus.FAILED
        log_row.error = f"{type(exc).__name__}: {exc}"
        log.exception("SEMS sync crashed for plant %s", plant.id)
    finally:
        if own and client is not None:
            client.close()
        log_row.finished_at = datetime.now(UTC)
        log_row.details = details
        db.add(log_row)
        db.commit()
    return log_row


def sync_all_sems_plants(db: Session, mode: str = "incremental") -> list[SyncLog]:
    s = get_settings()
    if not s.sems_configured:
        return []
    plants = list(db.scalars(select(Plant).where(Plant.is_demo.is_(False))))
    out = []
    with make_client() as client:
        for p in plants:
            out.append(run_sems_sync(db, p, mode=mode, client=client))
    return out


def sems_status(db: Session, plant: Plant) -> dict[str, Any]:
    s = get_settings()
    last = db.scalar(
        select(SyncLog)
        .where(SyncLog.plant_id == plant.id, SyncLog.source == "sems")
        .order_by(desc(SyncLog.started_at))
        .limit(1)
    )
    last_ok = db.scalar(
        select(SyncLog)
        .where(SyncLog.plant_id == plant.id, SyncLog.source == "sems", SyncLog.status == SyncStatus.SUCCESS)
        .order_by(desc(SyncLog.started_at))
        .limit(1)
    )
    latest_reading = db.scalar(
        select(EnergyReading)
        .where(EnergyReading.plant_id == plant.id, EnergyReading.source == DataSource.SEMS_API)
        .order_by(desc(EnergyReading.ts))
        .limit(1)
    )
    sid = plant.sems_station_id or s.sems_station_id
    return {
        "configured": s.sems_configured,
        "scheduled": s.sems_sync_enabled and s.sems_configured,
        "interval_seconds": s.sems_sync_interval_seconds,
        "api_kind": "Unofficial SEMS+ app/web API (normal SEMS account). GoodWe OpenAPI is not available for household accounts.",
        "station_id_masked": (sid[:4] + "…" + sid[-4:]) if sid else None,
        "last_sync": {
            "status": last.status.value,
            "mode": last.mode,
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "message": last.message,
            "error": last.error,
        }
        if last
        else None,
        "last_successful_sync_at": last_ok.finished_at.isoformat()
        if last_ok and last_ok.finished_at
        else None,
        "latest_reading": {
            "ts": latest_reading.ts.isoformat(),
            "power_w": latest_reading.power_w,
            "cumulative_energy_kwh": latest_reading.cumulative_energy_kwh,
            "temperature_c": latest_reading.temperature_c,
            "age_minutes": round((datetime.now(UTC) - latest_reading.ts).total_seconds() / 60),
        }
        if latest_reading
        else None,
        "has_smart_meter": (last_ok.details or {}).get("has_smart_meter") if last_ok else None,
    }
