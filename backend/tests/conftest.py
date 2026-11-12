"""Shared fixtures.

Tests run against a dedicated PostgreSQL database (``solar_test``) created from the
configured server so that enum types, NULLS NOT DISTINCT constraints and upserts are
exercised for real. Set TEST_DATABASE_URL to override.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("SEMS_SYNC_ENABLED", "false")
os.environ.setdefault("WEATHER_SYNC_ENABLED", "false")
os.environ.setdefault("ML_NIGHTLY_ENABLED", "false")
os.environ.setdefault("LLM_PROVIDER", "none")

from app.core.config import get_settings  # noqa: E402

_base_url = get_settings().database_url
TEST_DB_URL = os.environ.get("TEST_DATABASE_URL") or _base_url.rsplit("/", 1)[0] + "/solar_test"


def _ensure_test_db() -> None:
    admin_url = _base_url.rsplit("/", 1)[0] + "/postgres"
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname='solar_test'")).scalar()
        if not exists:
            conn.execute(text("CREATE DATABASE solar_test"))
    eng.dispose()


@pytest.fixture(scope="session")
def engine():
    _ensure_test_db()
    from app.db import models  # noqa: F401
    from app.db.base import Base

    eng = create_engine(TEST_DB_URL, future=True, connect_args={"options": "-c timezone=UTC"})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine) -> Iterator[Session]:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        # truncate everything between tests (fast, keeps enums)
        with engine.begin() as conn:
            conn.execute(text("TRUNCATE users, plants RESTART IDENTITY CASCADE"))


@pytest.fixture()
def client(engine, db):
    """FastAPI TestClient wired to the test database."""
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def _override():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    import app.main as main_mod

    original = main_mod.SessionLocal
    main_mod.SessionLocal = SessionLocal  # bootstrap admin into the test DB
    with TestClient(app) as c:
        yield c
    main_mod.SessionLocal = original
    app.dependency_overrides.clear()


@pytest.fixture()
def auth(client):
    s = get_settings()
    r = client.post("/api/auth/login", json={"email": s.admin_email, "password": s.admin_password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def plant(client, auth):
    r = client.post(
        "/api/plants",
        headers=auth,
        json={
            "name": "Test plant",
            "capacity_kw": 5.0,
            "timezone": "Asia/Kolkata",
            "latitude": 18.5,
            "longitude": 73.8,
            "panel_tilt_deg": 18,
            "panel_azimuth_deg": 45,
            "currency": "INR",
            "tariff_import_per_kwh": 8.0,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def seed_days(db: Session, plant_id: int, n: int = 70, start: date | None = None) -> list[date]:
    """Insert n synthetic-but-plausible complete days with observed weather (labelled synthetic)."""
    import math

    from app.db.models import DataSource, WeatherDaily, WeatherKind
    from app.services.energy_store import upsert_daily_energy

    start = start or (date.today() - timedelta(days=n))
    days = [start + timedelta(days=i) for i in range(n)]
    daily_rows, wx = [], []
    for i, d in enumerate(days):
        cloud = 20 + 60 * (0.5 + 0.5 * math.sin(i / 3.0))
        gti = 6500 * (1 - 0.7 * cloud / 100)
        gen = max(0.0, 5 * gti / 1000 * 0.55 + (1.5 if i % 7 == 0 else 0) - (8 if i == n - 20 else 0))
        daily_rows.append(
            {
                "plant_id": plant_id,
                "date": d,
                "generation_kwh": round(gen, 2),
                "source": DataSource.CSV_IMPORT,
                "is_complete": True,
            }
        )
        wx.append(
            WeatherDaily(
                plant_id=plant_id,
                date=d,
                kind=WeatherKind.OBSERVED,
                source="test",
                shortwave_radiation_sum=gti * 3.6 / 1000,
                gti_sum_wh_m2=gti,
                sunshine_duration_s=36000 * (1 - cloud / 100),
                temperature_max=30,
                temperature_mean=26,
                cloud_cover_mean=cloud,
                humidity_mean=50,
                wind_speed_max=10,
                precipitation_sum=0,
            )
        )
    upsert_daily_energy(db, daily_rows)
    db.add_all(wx)
    db.commit()
    return days
