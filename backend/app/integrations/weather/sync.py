"""Weather synchronisation: observed history (ERA5 + archived forecasts) and forward forecasts.

Provenance rules
----------------
* kind=observed, source=open-meteo-era5                  : reanalysis (authoritative, ~5 day delay)
* kind=observed, source=open-meteo-historical-forecast   : archived model analysis used to bridge
                                                          the ERA5 delay; replaced by ERA5 later
* kind=forecast, issued_at=<run time>                     : forward-looking forecast; history is kept
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import DailyEnergy, Plant, SyncLog, SyncStatus, WeatherDaily, WeatherHourly, WeatherKind
from app.integrations.weather.open_meteo import (
    OpenMeteoClient,
    OpenMeteoError,
    OpenMeteoRateLimitError,
    WeatherBundle,
)

log = logging.getLogger(__name__)

ERA5_DELAY_DAYS = 6
DEFAULT_HISTORY_DAYS = 400
ARCHIVE_CHUNK_DAYS = 366
HOURLY_COLS = (
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
    "global_tilted_irradiance",
    "cloud_cover",
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "precipitation",
)


class WeatherSyncError(RuntimeError):
    pass


def _mean(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _sum(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) if vals else None


def _max(values: list[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return max(vals) if vals else None


def bundle_to_rows(
    bundle: WeatherBundle,
    plant_id: int,
    kind: WeatherKind,
    issued_at: datetime | None,
    tz_name: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert Open-Meteo arrays into hourly + daily rows (UTC timestamps, local dates)."""
    tz = ZoneInfo(bundle.timezone or tz_name)
    hourly_rows: list[dict[str, Any]] = []
    per_day: dict[date, dict[str, list[float | None]]] = {}
    times = bundle.hourly.get("time", [])
    for i, t in enumerate(times):
        local_naive = datetime.fromisoformat(t)
        local = local_naive.replace(tzinfo=tz)
        row: dict[str, Any] = {
            "plant_id": plant_id,
            "ts": local.astimezone(UTC),
            "kind": kind,
            "issued_at": issued_at,
            "source": bundle.source,
        }
        for col in HOURLY_COLS:
            vals = bundle.hourly.get(col)
            row[col] = vals[i] if vals and i < len(vals) else None
        hourly_rows.append(row)
        bucket = per_day.setdefault(local.date(), {c: [] for c in HOURLY_COLS})
        for col in HOURLY_COLS:
            bucket[col].append(row[col])

    daily_rows: list[dict[str, Any]] = []
    daily_times = bundle.daily.get("time", [])
    daily_index = {date.fromisoformat(d): i for i, d in enumerate(daily_times)}

    def dval(name: str, d: date):
        vals = bundle.daily.get(name)
        i = daily_index.get(d)
        return vals[i] if vals is not None and i is not None and i < len(vals) else None

    for d, bucket in sorted(per_day.items()):
        hourly_count = len(bucket["shortwave_radiation"])
        if hourly_count < 20:
            # Partial day (e.g. first/last day of a chunk) - skip daily aggregate, hourly rows still stored
            if d not in daily_index:
                continue
        gti = _sum(bucket["global_tilted_irradiance"])
        daily_rows.append(
            {
                "plant_id": plant_id,
                "date": d,
                "kind": kind,
                "issued_at": issued_at,
                "source": bundle.source,
                "shortwave_radiation_sum": dval("shortwave_radiation_sum", d)
                if dval("shortwave_radiation_sum", d) is not None
                else (_sum(bucket["shortwave_radiation"]) or 0) * 3600 / 1e6,
                "gti_sum_wh_m2": gti,
                "sunshine_duration_s": dval("sunshine_duration", d),
                "temperature_max": dval("temperature_2m_max", d) or _max(bucket["temperature_2m"]),
                "temperature_min": dval("temperature_2m_min", d)
                if dval("temperature_2m_min", d) is not None
                else (min([v for v in bucket["temperature_2m"] if v is not None], default=None)),
                "temperature_mean": _mean(bucket["temperature_2m"]),
                "cloud_cover_mean": dval("cloud_cover_mean", d)
                if dval("cloud_cover_mean", d) is not None
                else _mean(bucket["cloud_cover"]),
                "humidity_mean": _mean(bucket["relative_humidity_2m"]),
                "wind_speed_max": _max(bucket["wind_speed_10m"]),
                "precipitation_sum": dval("precipitation_sum", d)
                if dval("precipitation_sum", d) is not None
                else _sum(bucket["precipitation"]),
                "weather_code": dval("weather_code", d),
            }
        )
    return hourly_rows, daily_rows


def upsert_weather(db: Session, hourly: list[dict[str, Any]], daily: list[dict[str, Any]]) -> dict[str, int]:
    n_h = n_d = 0
    for i in range(0, len(hourly), 2000):
        chunk = hourly[i : i + 2000]
        stmt = pg_insert(WeatherHourly).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_weather_hourly_plant_id_ts_kind_issued_at",
            set_={c: getattr(stmt.excluded, c) for c in HOURLY_COLS + ("source",)},
        )
        db.execute(stmt)
        n_h += len(chunk)
    daily_cols = (
        "source",
        "shortwave_radiation_sum",
        "gti_sum_wh_m2",
        "sunshine_duration_s",
        "temperature_max",
        "temperature_min",
        "temperature_mean",
        "cloud_cover_mean",
        "humidity_mean",
        "wind_speed_max",
        "precipitation_sum",
        "weather_code",
    )
    for i in range(0, len(daily), 2000):
        chunk = daily[i : i + 2000]
        stmt = pg_insert(WeatherDaily).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_weather_daily_plant_id_date_kind_issued_at",
            set_={c: getattr(stmt.excluded, c) for c in daily_cols},
        )
        db.execute(stmt)
        n_d += len(chunk)
    return {"hourly": n_h, "daily": n_d}


def _plant_today(plant: Plant) -> date:
    return datetime.now(ZoneInfo(plant.timezone or "UTC")).date()


def _observed_coverage(
    db: Session, plant_id: int, source: str | None = None
) -> tuple[date | None, date | None]:
    stmt = select(func.min(WeatherDaily.date), func.max(WeatherDaily.date)).where(
        WeatherDaily.plant_id == plant_id, WeatherDaily.kind == WeatherKind.OBSERVED
    )
    if source:
        stmt = stmt.where(WeatherDaily.source == source)
    mn, mx = db.execute(stmt).one()
    return mn, mx


def sync_observed_weather(
    db: Session,
    plant: Plant,
    client: OpenMeteoClient | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    log_row: SyncLog | None = None,
) -> dict[str, Any]:
    """Fetch ERA5 history (incremental by default) and bridge the recent gap with archived forecasts."""
    if plant.latitude is None or plant.longitude is None:
        raise WeatherSyncError("Plant location (latitude/longitude) is not set")
    own_client = client is None
    client = client or OpenMeteoClient()
    today = _plant_today(plant)
    era5_end = min(end or today, today - timedelta(days=ERA5_DELAY_DAYS))
    _, last_era5 = _observed_coverage(db, plant.id, "open-meteo-era5")
    if start is None:
        earliest_energy = db.scalar(
            select(func.min(DailyEnergy.date)).where(DailyEnergy.plant_id == plant.id)
        )
        start = min(earliest_energy or today, today - timedelta(days=DEFAULT_HISTORY_DAYS))
        if last_era5 is not None:
            start = max(start, last_era5 + timedelta(days=1))
    counts = {"hourly": 0, "daily": 0, "requests": 0}
    try:
        cursor = start
        while cursor <= era5_end:
            chunk_end = min(cursor + timedelta(days=ARCHIVE_CHUNK_DAYS - 1), era5_end)
            bundle = client.archive(
                plant.latitude,
                plant.longitude,
                plant.timezone,
                cursor,
                chunk_end,
                tilt=plant.panel_tilt_deg,
                azimuth=plant.panel_azimuth_deg,
            )
            counts["requests"] += 1
            h, d = bundle_to_rows(bundle, plant.id, WeatherKind.OBSERVED, None, plant.timezone)
            res = upsert_weather(db, h, d)
            counts["hourly"] += res["hourly"]
            counts["daily"] += res["daily"]
            db.commit()
            cursor = chunk_end + timedelta(days=1)

        # Bridge: days after ERA5 coverage up to today (inclusive) via archived forecasts
        bridge_start = max(era5_end + timedelta(days=1), start)
        bridge_end = end or today
        if bridge_start <= bridge_end:
            bundle = client.historical_forecast(
                plant.latitude,
                plant.longitude,
                plant.timezone,
                bridge_start,
                bridge_end,
                tilt=plant.panel_tilt_deg,
                azimuth=plant.panel_azimuth_deg,
            )
            counts["requests"] += 1
            h, d = bundle_to_rows(bundle, plant.id, WeatherKind.OBSERVED, None, plant.timezone)
            # never let bridge data overwrite ERA5 rows
            h = [
                r for r in h if r["ts"].astimezone(ZoneInfo(plant.timezone)).date() > (last_era5 or date.min)
            ]
            d = [r for r in d if r["date"] > (last_era5 or date.min)]
            res = upsert_weather(db, h, d)
            counts["hourly"] += res["hourly"]
            counts["daily"] += res["daily"]
            db.commit()
    finally:
        if own_client:
            client.close()
    counts["range"] = {"start": start.isoformat(), "end": (end or today).isoformat()}
    return counts


def sync_forecast_weather(
    db: Session, plant: Plant, client: OpenMeteoClient | None = None, *, days: int = 7
) -> dict[str, Any]:
    if plant.latitude is None or plant.longitude is None:
        raise WeatherSyncError("Plant location (latitude/longitude) is not set")
    own_client = client is None
    client = client or OpenMeteoClient()
    try:
        bundle = client.forecast(
            plant.latitude,
            plant.longitude,
            plant.timezone,
            forecast_days=days,
            tilt=plant.panel_tilt_deg,
            azimuth=plant.panel_azimuth_deg,
        )
    finally:
        if own_client:
            client.close()
    issued_at = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    h, d = bundle_to_rows(bundle, plant.id, WeatherKind.FORECAST, issued_at, plant.timezone)
    res = upsert_weather(db, h, d)
    db.commit()
    res["issued_at"] = issued_at.isoformat()
    res["days"] = len(d)
    return res


def _latest_forecast_issued_at(db: Session, plant_id: int) -> datetime | None:
    return db.scalar(
        select(func.max(WeatherDaily.issued_at)).where(
            WeatherDaily.plant_id == plant_id, WeatherDaily.kind == WeatherKind.FORECAST
        )
    )


def _error_text(exc: Exception) -> str:
    if isinstance(exc, (OpenMeteoError, WeatherSyncError)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def run_weather_sync(db: Session, plant: Plant, *, mode: str = "incremental", **kwargs) -> SyncLog:
    """Run observed + forecast sync for a plant with a SyncLog record.

    The two halves are independent: if one fails the other's data is still kept and the
    run is logged as PARTIAL. The forecast request is skipped while the stored forecast
    is younger than ``weather_forecast_min_interval_seconds``.
    """
    log_row = SyncLog(plant_id=plant.id, source="weather", mode=mode, status=SyncStatus.RUNNING)
    db.add(log_row)
    db.commit()
    details: dict[str, Any] = {}
    errors: dict[str, str] = {}
    rate_limited = False
    client = OpenMeteoClient()
    try:
        if mode == "full":
            kwargs.setdefault(
                "start",
                plant.installation_date or (_plant_today(plant) - timedelta(days=DEFAULT_HISTORY_DAYS)),
            )
        try:
            details["observed"] = sync_observed_weather(db, plant, client, **kwargs)
        except Exception as exc:
            db.rollback()
            errors["observed"] = _error_text(exc)
            rate_limited = isinstance(exc, OpenMeteoRateLimitError)
            if not isinstance(exc, (OpenMeteoError, WeatherSyncError)):  # pragma: no cover
                log.exception("Observed weather sync crashed for plant %s", plant.id)

        min_age = timedelta(seconds=get_settings().weather_forecast_min_interval_seconds)
        latest = _latest_forecast_issued_at(db, plant.id)
        if latest is not None and datetime.now(UTC) - latest < min_age:
            details["forecast"] = {"skipped": True, "latest_issued_at": latest.isoformat()}
        elif rate_limited:
            # Same quota, so the forecast call would be rejected too; don't spend a request on it.
            errors["forecast"] = "skipped: Open-Meteo rate limit reached on the observed request"
        else:
            try:
                details["forecast"] = sync_forecast_weather(db, plant, client)
            except Exception as exc:
                db.rollback()
                errors["forecast"] = _error_text(exc)
                if not isinstance(exc, (OpenMeteoError, WeatherSyncError)):  # pragma: no cover
                    log.exception("Forecast weather sync crashed for plant %s", plant.id)

        obs, fc = details.get("observed", {}), details.get("forecast", {})
        log_row.records_fetched = obs.get("hourly", 0) + fc.get("hourly", 0)
        log_row.records_inserted = obs.get("daily", 0) + fc.get("daily", 0)
        parts = [f"Observed {obs['daily']} days" if "observed" in details else "observed failed"]
        if fc.get("skipped"):
            parts.append("forecast still fresh, not re-fetched")
        elif "forecast" in details:
            parts.append(f"forecast {fc['days']} days")
        else:
            parts.append("forecast failed")
        log_row.message = ", ".join(parts)
        if not errors:
            log_row.status = SyncStatus.SUCCESS
        elif len(errors) == 2:
            log_row.status = SyncStatus.FAILED
        else:
            log_row.status = SyncStatus.PARTIAL
        if errors:
            log_row.error = "; ".join(f"{k}: {v}" for k, v in errors.items())
            log.warning("Weather sync %s for plant %s: %s", log_row.status.value, plant.id, log_row.error)
    except Exception as exc:  # pragma: no cover
        db.rollback()
        log_row.status = SyncStatus.FAILED
        log_row.error = _error_text(exc)
        log.exception("Weather sync crashed for plant %s", plant.id)
    finally:
        client.close()
        log_row.finished_at = datetime.now(UTC)
        log_row.details = details
        db.add(log_row)
        db.commit()
    return log_row


def sync_weather_all_plants(db: Session) -> list[SyncLog]:
    plants = list(db.scalars(select(Plant).where(Plant.latitude.is_not(None), Plant.longitude.is_not(None))))
    return [run_weather_sync(db, p) for p in plants]
