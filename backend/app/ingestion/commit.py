"""Persist normalised import data via the energy store (idempotent upserts)."""

from __future__ import annotations

from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.db.models import DataSource
from app.ingestion.normalize import INTERVAL_COLS, NormalizedData
from app.services.energy_store import upsert_daily_energy, upsert_energy_readings


def _none(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def commit_import(db: Session, data: NormalizedData, *, plant_id: int, import_job_id: int) -> dict[str, Any]:
    result: dict[str, Any] = {
        "readings": {"inserted": 0, "updated": 0},
        "daily": {"inserted": 0, "updated": 0, "skipped": 0},
    }
    if data.kind == "interval" and data.interval is not None and not data.interval.empty:
        rows = []
        for rec in data.interval.to_dict(orient="records"):
            row = {
                "plant_id": plant_id,
                "inverter_id": None,
                "ts": rec["ts"].to_pydatetime(),
                "source": DataSource.CSV_IMPORT,
                "quality_flags": rec.get("quality_flags") or None,
                "import_job_id": import_job_id,
            }
            for c in INTERVAL_COLS:
                v = _none(rec.get(c))
                row[c] = float(v) if v is not None else None
            rows.append(row)
        result["readings"] = upsert_energy_readings(db, rows)

    if data.daily is not None and not data.daily.empty:
        rows = []
        for rec in data.daily.to_dict(orient="records"):
            gen = _none(rec.get("generation_kwh"))
            if gen is None:
                continue
            rows.append(
                {
                    "plant_id": plant_id,
                    "date": rec["date"],
                    "generation_kwh": float(gen),
                    "peak_power_w": _none(rec.get("peak_power_w")),
                    "consumption_kwh": _none(rec.get("consumption_kwh")),
                    "exported_kwh": _none(rec.get("exported_kwh")),
                    "imported_kwh": _none(rec.get("imported_kwh")),
                    "reading_count": int(rec["reading_count"])
                    if _none(rec.get("reading_count")) is not None
                    else None,
                    "source": DataSource.CSV_IMPORT,
                    "derived_from": rec.get("derived_from") or "import_daily_value",
                    "is_complete": True,
                    "quality_flags": rec.get("quality_flags") or None,
                    "import_job_id": import_job_id,
                }
            )
        result["daily"] = upsert_daily_energy(db, rows)
    return result
