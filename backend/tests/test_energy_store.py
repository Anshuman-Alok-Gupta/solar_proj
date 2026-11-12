from datetime import UTC, date, datetime

from sqlalchemy import select

from app.db.models import DailyEnergy, DataSource, EnergyReading, Plant, User
from app.services.energy_store import upsert_daily_energy, upsert_energy_readings


def _plant(db) -> Plant:
    u = User(email="t@example.com", hashed_password="x")
    db.add(u)
    db.flush()
    p = Plant(owner_id=u.id, name="p", timezone="UTC")
    db.add(p)
    db.commit()
    return p


def test_daily_upsert_source_precedence(db):
    p = _plant(db)
    d = date(2026, 10, 1)
    r = upsert_daily_energy(
        db, [{"plant_id": p.id, "date": d, "generation_kwh": 10.0, "source": DataSource.SYNTHETIC}]
    )
    assert r == {"inserted": 1, "updated": 0, "skipped": 0}
    # import overrides synthetic
    r = upsert_daily_energy(
        db, [{"plant_id": p.id, "date": d, "generation_kwh": 11.0, "source": DataSource.CSV_IMPORT}]
    )
    assert r["updated"] == 1
    # API overrides import
    r = upsert_daily_energy(
        db, [{"plant_id": p.id, "date": d, "generation_kwh": 12.0, "source": DataSource.SEMS_API}]
    )
    assert r["updated"] == 1
    # import must NOT override API
    r = upsert_daily_energy(
        db, [{"plant_id": p.id, "date": d, "generation_kwh": 1.0, "source": DataSource.CSV_IMPORT}]
    )
    assert r["skipped"] == 1
    db.commit()
    row = db.scalar(select(DailyEnergy).where(DailyEnergy.plant_id == p.id, DailyEnergy.date == d))
    assert row.generation_kwh == 12.0 and row.source == DataSource.SEMS_API
    # same source overwrites itself (revised value)
    upsert_daily_energy(
        db, [{"plant_id": p.id, "date": d, "generation_kwh": 12.5, "source": DataSource.SEMS_API}]
    )
    db.commit()
    db.refresh(row)
    assert row.generation_kwh == 12.5


def test_reading_upsert_dedupes_null_inverter(db):
    p = _plant(db)
    ts = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
    rows = [
        {"plant_id": p.id, "inverter_id": None, "ts": ts, "power_w": 100.0, "source": DataSource.CSV_IMPORT}
    ]
    assert upsert_energy_readings(db, rows)["inserted"] == 1
    rows = [
        {"plant_id": p.id, "inverter_id": None, "ts": ts, "power_w": 120.0, "source": DataSource.CSV_IMPORT}
    ]
    assert upsert_energy_readings(db, rows)["updated"] == 1
    db.commit()
    all_rows = db.scalars(select(EnergyReading).where(EnergyReading.plant_id == p.id)).all()
    assert len(all_rows) == 1 and all_rows[0].power_w == 120.0
