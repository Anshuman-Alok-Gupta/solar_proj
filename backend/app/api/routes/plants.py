from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select

from app.api.deps import DB, CurrentUser, PlantDep, user_plants
from app.db.models import DailyEnergy, DataSource, EnergyReading, Plant
from app.schemas.plant import InverterOut, PlantCreate, PlantOut, PlantUpdate

router = APIRouter(prefix="/plants", tags=["plants"])


@router.get("", response_model=list[PlantOut])
def list_plants(db: DB, user: CurrentUser) -> list[Plant]:
    return user_plants(db, user)


@router.post("", response_model=PlantOut, status_code=status.HTTP_201_CREATED)
def create_plant(payload: PlantCreate, db: DB, user: CurrentUser) -> Plant:
    plant = Plant(owner_id=user.id, **payload.model_dump())
    db.add(plant)
    db.commit()
    db.refresh(plant)
    return plant


@router.get("/{plant_id}", response_model=PlantOut)
def get_plant(plant: PlantDep) -> Plant:
    return plant


@router.patch("/{plant_id}", response_model=PlantOut)
def update_plant(payload: PlantUpdate, plant: PlantDep, db: DB) -> Plant:
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(plant, k, v)
    db.commit()
    db.refresh(plant)
    return plant


@router.delete("/{plant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plant(plant: PlantDep, db: DB) -> None:
    db.delete(plant)
    db.commit()


@router.get("/{plant_id}/inverters", response_model=list[InverterOut])
def list_inverters(plant: PlantDep):
    return plant.inverters


@router.get("/{plant_id}/data-coverage")
def data_coverage(plant: PlantDep, db: DB) -> dict:
    """What data exists for this plant, by source. Drives empty states and the data page."""
    daily = db.execute(
        select(
            DailyEnergy.source,
            func.count(),
            func.min(DailyEnergy.date),
            func.max(DailyEnergy.date),
            func.sum(DailyEnergy.generation_kwh),
        )
        .where(DailyEnergy.plant_id == plant.id)
        .group_by(DailyEnergy.source)
    ).all()
    readings = db.execute(
        select(EnergyReading.source, func.count(), func.min(EnergyReading.ts), func.max(EnergyReading.ts))
        .where(EnergyReading.plant_id == plant.id)
        .group_by(EnergyReading.source)
    ).all()
    return {
        "plant_id": plant.id,
        "daily": [
            {
                "source": s.value,
                "days": c,
                "first_date": mn.isoformat() if mn else None,
                "last_date": mx.isoformat() if mx else None,
                "total_kwh": round(float(total or 0), 2),
            }
            for s, c, mn, mx, total in daily
        ],
        "readings": [
            {
                "source": s.value,
                "count": c,
                "first_ts": mn.isoformat() if mn else None,
                "last_ts": mx.isoformat() if mx else None,
            }
            for s, c, mn, mx in readings
        ],
        "has_real_data": any(s.value != "synthetic" for s, *_ in daily),
        "has_any_data": bool(daily),
    }


@router.post("/{plant_id}/clear-synthetic")
def clear_synthetic(plant: PlantDep, db: DB) -> dict:
    """Remove synthetic demo data for a plant (never touches real readings)."""
    d1 = db.execute(
        delete(DailyEnergy).where(
            DailyEnergy.plant_id == plant.id, DailyEnergy.source == DataSource.SYNTHETIC
        )
    ).rowcount
    d2 = db.execute(
        delete(EnergyReading).where(
            EnergyReading.plant_id == plant.id, EnergyReading.source == DataSource.SYNTHETIC
        )
    ).rowcount
    db.commit()
    if d1 == 0 and d2 == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No synthetic data found for this plant")
    return {"daily_deleted": d1, "readings_deleted": d2}
