from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, select

from app.api.deps import DB, PlantDep
from app.db.models import SyncLog, WeatherDaily, WeatherHourly, WeatherKind
from app.integrations.weather.sync import run_weather_sync
from app.schemas.common import SyncLogOut

router = APIRouter(prefix="/plants/{plant_id}/weather", tags=["weather"])


def _daily_out(r: WeatherDaily) -> dict:
    return {
        "date": r.date.isoformat(),
        "kind": r.kind.value,
        "issued_at": r.issued_at.isoformat() if r.issued_at else None,
        "source": r.source,
        "shortwave_radiation_sum_mj": r.shortwave_radiation_sum,
        "gti_sum_wh_m2": r.gti_sum_wh_m2,
        "sunshine_hours": round(r.sunshine_duration_s / 3600, 2)
        if r.sunshine_duration_s is not None
        else None,
        "temperature_max": r.temperature_max,
        "temperature_min": r.temperature_min,
        "temperature_mean": r.temperature_mean,
        "cloud_cover_mean": r.cloud_cover_mean,
        "humidity_mean": r.humidity_mean,
        "wind_speed_max": r.wind_speed_max,
        "precipitation_sum": r.precipitation_sum,
        "weather_code": r.weather_code,
    }


@router.get("/daily")
def weather_daily(
    plant: PlantDep,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    kind: str = Query("observed", pattern="^(observed|forecast)$"),
) -> dict:
    end = end or date.today() + (timedelta(days=7) if kind == "forecast" else timedelta(days=0))
    start = start or end - timedelta(days=90)
    k = WeatherKind(kind)
    stmt = select(WeatherDaily).where(
        WeatherDaily.plant_id == plant.id,
        WeatherDaily.kind == k,
        WeatherDaily.date >= start,
        WeatherDaily.date <= end,
    )
    if k == WeatherKind.FORECAST:
        # latest issued forecast per date
        latest = (
            select(WeatherDaily.date, WeatherDaily.issued_at)
            .where(WeatherDaily.plant_id == plant.id, WeatherDaily.kind == k)
            .order_by(WeatherDaily.date, desc(WeatherDaily.issued_at))
            .distinct(WeatherDaily.date)
            .subquery()
        )
        stmt = stmt.join(
            latest, (WeatherDaily.date == latest.c.date) & (WeatherDaily.issued_at == latest.c.issued_at)
        )
    rows = list(db.scalars(stmt.order_by(WeatherDaily.date)))
    return {
        "plant_id": plant.id,
        "kind": kind,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "items": [_daily_out(r) for r in rows],
    }


@router.get("/hourly")
def weather_hourly(
    plant: PlantDep,
    db: DB,
    day: date,
    kind: str = Query("observed", pattern="^(observed|forecast)$"),
) -> dict:
    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(plant.timezone or "UTC")
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz).astimezone(UTC)
    end = start + timedelta(days=1)
    k = WeatherKind(kind)
    stmt = select(WeatherHourly).where(
        WeatherHourly.plant_id == plant.id,
        WeatherHourly.kind == k,
        WeatherHourly.ts >= start,
        WeatherHourly.ts < end,
    )
    if k == WeatherKind.FORECAST:
        latest_issue = db.scalar(
            select(WeatherHourly.issued_at)
            .where(
                WeatherHourly.plant_id == plant.id,
                WeatherHourly.kind == k,
                WeatherHourly.ts >= start,
                WeatherHourly.ts < end,
            )
            .order_by(desc(WeatherHourly.issued_at))
            .limit(1)
        )
        stmt = stmt.where(WeatherHourly.issued_at == latest_issue)
    rows = list(db.scalars(stmt.order_by(WeatherHourly.ts)))
    return {
        "plant_id": plant.id,
        "day": day.isoformat(),
        "kind": kind,
        "items": [
            {
                "ts": r.ts.isoformat(),
                "local_time": r.ts.astimezone(tz).strftime("%H:%M"),
                "source": r.source,
                "ghi": r.shortwave_radiation,
                "gti": r.global_tilted_irradiance,
                "dni": r.direct_normal_irradiance,
                "cloud_cover": r.cloud_cover,
                "temperature": r.temperature_2m,
                "humidity": r.relative_humidity_2m,
                "wind_speed": r.wind_speed_10m,
                "precipitation": r.precipitation,
            }
            for r in rows
        ],
    }


@router.post("/sync", response_model=SyncLogOut)
def trigger_weather_sync(
    plant: PlantDep, db: DB, mode: str = Query("incremental", pattern="^(incremental|full)$")
):
    if plant.latitude is None or plant.longitude is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Set the plant latitude/longitude in Settings before syncing weather"
        )
    return run_weather_sync(db, plant, mode=mode)


@router.get("/sync-logs", response_model=list[SyncLogOut])
def weather_sync_logs(plant: PlantDep, db: DB, limit: int = Query(20, le=200)):
    return list(
        db.scalars(
            select(SyncLog)
            .where(SyncLog.plant_id == plant.id, SyncLog.source == "weather")
            .order_by(desc(SyncLog.started_at))
            .limit(limit)
        )
    )
