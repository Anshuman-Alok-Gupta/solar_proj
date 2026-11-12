from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Query

from app.api.deps import DB, PlantDep
from app.services import analytics as svc

router = APIRouter(prefix="/plants/{plant_id}/analytics", tags=["analytics"])


def _range(plant, start: date | None, end: date | None, default_days: int) -> tuple[date, date]:
    end = end or svc.plant_today(plant)
    start = start or end - timedelta(days=default_days - 1)
    return start, end


@router.get("/summary")
def summary(plant: PlantDep, db: DB, include_synthetic: bool = False) -> dict:
    return svc.summary(db, plant, include_synthetic=include_synthetic)


@router.get("/daily")
def daily(
    plant: PlantDep,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    include_synthetic: bool = False,
) -> dict:
    s, e = _range(plant, start, end, 90)
    return svc.daily_series(db, plant, s, e, include_synthetic=include_synthetic)


@router.get("/monthly")
def monthly(plant: PlantDep, db: DB, include_synthetic: bool = False) -> dict:
    return svc.monthly_series(db, plant, include_synthetic=include_synthetic)


@router.get("/weekly")
def weekly(
    plant: PlantDep, db: DB, weeks: int = Query(26, ge=1, le=260), include_synthetic: bool = False
) -> dict:
    return svc.weekly_series(db, plant, weeks, include_synthetic=include_synthetic)


@router.get("/distribution")
def distribution(
    plant: PlantDep,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    bins: int = Query(12, ge=3, le=50),
    include_synthetic: bool = False,
) -> dict:
    s, e = _range(plant, start, end, 365)
    return svc.distribution(db, plant, s, e, bins, include_synthetic=include_synthetic)


@router.get("/weather-correlation")
def weather_correlation(
    plant: PlantDep,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    include_synthetic: bool = False,
) -> dict:
    s, e = _range(plant, start, end, 365)
    return svc.weather_correlation(db, plant, s, e, include_synthetic=include_synthetic)


@router.get("/compare")
def compare(plant: PlantDep, db: DB, include_synthetic: bool = False) -> dict:
    return svc.compare_periods(db, plant, include_synthetic=include_synthetic)


@router.get("/intraday")
def intraday(plant: PlantDep, db: DB, day: date | None = None) -> dict:
    return svc.intraday(db, plant, day or svc.plant_today(plant))


@router.get("/savings")
def savings(
    plant: PlantDep,
    db: DB,
    start: date | None = None,
    end: date | None = None,
    include_synthetic: bool = False,
) -> dict:
    return svc.savings(db, plant, start, end, include_synthetic=include_synthetic)


@router.get("/savings/monthly")
def savings_monthly(plant: PlantDep, db: DB, include_synthetic: bool = False) -> dict:
    return svc.monthly_savings(db, plant, include_synthetic=include_synthetic)
