"""Idempotent persistence of energy data with explicit source precedence.

Precedence when several sources describe the same plant/date:
    sems_api (live/API) > csv_import (historical import) > synthetic (demo)
A lower-precedence source never overwrites a higher one; the same source
overwrites itself (latest wins).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, case, cast, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import DailyEnergy, DataSource, EnergyReading

SOURCE_RANK: dict[str, int] = {
    DataSource.SEMS_API.value: 3,
    DataSource.CSV_IMPORT.value: 2,
    DataSource.SYNTHETIC.value: 1,
}

DAILY_UPDATE_COLS = (
    "generation_kwh",
    "peak_power_w",
    "consumption_kwh",
    "self_consumed_kwh",
    "exported_kwh",
    "imported_kwh",
    "reading_count",
    "source",
    "derived_from",
    "is_complete",
    "quality_flags",
    "import_job_id",
    "sync_log_id",
)

READING_UPDATE_COLS = (
    "power_w",
    "energy_kwh",
    "cumulative_energy_kwh",
    "pv_voltage_v",
    "pv_current_a",
    "ac_voltage_v",
    "ac_current_a",
    "frequency_hz",
    "temperature_c",
    "source",
    "quality_flags",
    "import_job_id",
    "sync_log_id",
)


def _rank_expr(column):
    # ``source`` is a PostgreSQL enum; compare as text so the CASE works for both the
    # table column and the ``excluded`` pseudo-row.
    return case(SOURCE_RANK, value=cast(column, String), else_=0)


def _normalise_source(row: dict[str, Any]) -> None:
    src = row["source"]
    row["source"] = src.value if isinstance(src, DataSource) else src


def upsert_daily_energy(db: Session, rows: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Bulk upsert daily rows. Each row needs plant_id, date, generation_kwh, source."""
    rows = [r for r in rows if r.get("generation_kwh") is not None]
    if not rows:
        return {"inserted": 0, "updated": 0, "skipped": 0}
    for r in rows:
        _normalise_source(r)
        for col in DAILY_UPDATE_COLS:
            r.setdefault(col, None)
        r["derived_from"] = r["derived_from"] or "daily_value"
        r["is_complete"] = True if r["is_complete"] is None else r["is_complete"]

    plant_ids = {r["plant_id"] for r in rows}
    dates = {r["date"] for r in rows}
    existing = {
        (row.plant_id, row.date): row.source.value
        for row in db.scalars(
            select(DailyEnergy).where(DailyEnergy.plant_id.in_(plant_ids), DailyEnergy.date.in_(dates))
        )
    }
    inserted = updated = skipped = 0
    to_write: list[dict[str, Any]] = []
    for r in rows:
        key = (r["plant_id"], r["date"])
        if key not in existing:
            inserted += 1
            to_write.append(r)
        elif SOURCE_RANK.get(r["source"], 0) >= SOURCE_RANK.get(existing[key], 0):
            updated += 1
            to_write.append(r)
        else:
            skipped += 1

    for i in range(0, len(to_write), 2000):
        stmt = pg_insert(DailyEnergy).values(to_write[i : i + 2000])
        set_ = {c: getattr(stmt.excluded, c) for c in DAILY_UPDATE_COLS}
        set_["updated_at"] = datetime.now(UTC)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_daily_energy_plant_id_date",
            set_=set_,
            where=_rank_expr(stmt.excluded.source) >= _rank_expr(DailyEnergy.source),
        )
        db.execute(stmt)
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


def upsert_energy_readings(db: Session, rows: Iterable[dict[str, Any]], chunk: int = 2000) -> dict[str, int]:
    """Bulk upsert interval readings keyed by (plant_id, inverter_id, ts)."""
    rows = list(rows)
    if not rows:
        return {"inserted": 0, "updated": 0}
    for r in rows:
        _normalise_source(r)
        r.setdefault("inverter_id", None)
        for col in READING_UPDATE_COLS:
            r.setdefault(col, None)

    plant_ids = {r["plant_id"] for r in rows}
    ts_min = min(r["ts"] for r in rows)
    ts_max = max(r["ts"] for r in rows)
    existing_keys = set(
        db.execute(
            select(EnergyReading.plant_id, EnergyReading.inverter_id, EnergyReading.ts).where(
                EnergyReading.plant_id.in_(plant_ids),
                EnergyReading.ts >= ts_min,
                EnergyReading.ts <= ts_max,
            )
        ).all()
    )
    inserted = sum(1 for r in rows if (r["plant_id"], r["inverter_id"], r["ts"]) not in existing_keys)
    updated = len(rows) - inserted

    for i in range(0, len(rows), chunk):
        stmt = pg_insert(EnergyReading).values(rows[i : i + chunk])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_energy_readings_plant_id_inverter_id_ts",
            set_={c: getattr(stmt.excluded, c) for c in READING_UPDATE_COLS},
            where=_rank_expr(stmt.excluded.source) >= _rank_expr(EnergyReading.source),
        )
        db.execute(stmt)
    return {"inserted": inserted, "updated": updated}
