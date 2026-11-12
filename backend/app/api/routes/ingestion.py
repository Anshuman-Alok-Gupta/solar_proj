"""CSV / Excel import workflow: upload -> inspect -> preview (mapping + quality) -> commit."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.api.deps import DB, CurrentUser, PlantDep
from app.core.config import get_settings
from app.db.models import ImportJob, ImportStatus
from app.ingestion.commit import commit_import
from app.ingestion.inspect import ROLES, profile_table
from app.ingestion.normalize import ColumnMapping, MappingError, normalize
from app.ingestion.quality import clean_nan, run_quality_checks
from app.ingestion.readers import UnsupportedFileError, detect_format, load_table
from app.schemas.common import ORMModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/plants/{plant_id}/imports", tags=["imports"])

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


class ImportJobOut(ORMModel):
    id: int
    plant_id: int
    filename: str
    file_format: str
    sheet_name: str | None
    status: str
    inspection: dict[str, Any] | None
    column_mapping: dict[str, Any] | None
    quality_report: dict[str, Any] | None
    timezone: str | None
    rows_total: int | None
    rows_valid: int | None
    rows_rejected: int | None
    rows_inserted: int | None
    rows_updated: int | None
    rows_duplicate: int | None
    error: str | None
    created_at: datetime
    committed_at: datetime | None


class PreviewRequest(BaseModel):
    mapping: ColumnMapping
    sheet_name: str | None = None
    header_row: int | None = None


def _job(db, plant, job_id: int) -> ImportJob:
    job = db.get(ImportJob, job_id)
    if job is None or job.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import job not found")
    return job


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._()\- ]+", "_", Path(name).name)[:200]


@router.get("/roles")
def list_roles() -> dict:
    return {"roles": [{"role": r, **v} for r, v in ROLES.items()]}


@router.post("", response_model=ImportJobOut, status_code=status.HTTP_201_CREATED)
async def upload(plant: PlantDep, db: DB, user: CurrentUser, file: UploadFile = File(...)):
    settings = get_settings()
    try:
        fmt = detect_format(file.filename or "upload")
    except UnsupportedFileError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File larger than 50 MB")
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Empty file")
    target_dir = settings.upload_dir / f"plant_{plant.id}"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored = target_dir / f"{uuid.uuid4().hex}_{_safe_name(file.filename or 'upload')}"
    stored.write_bytes(content)

    job = ImportJob(
        plant_id=plant.id,
        user_id=user.id,
        filename=file.filename or stored.name,
        stored_path=str(stored),
        file_format=fmt,
        status=ImportStatus.UPLOADED,
        timezone=plant.timezone,
    )
    db.add(job)
    db.commit()
    try:
        table = load_table(stored)
        inspection = profile_table(table)
        job.inspection = clean_nan(inspection)
        job.sheet_name = table.sheet_name
        job.rows_total = int(len(table.df))
        job.column_mapping = inspection["suggested_mapping"]
    except (UnsupportedFileError, ValueError) as exc:
        job.status = ImportStatus.FAILED
        job.error = f"Could not read file: {exc}"
        log.warning("Import %s inspection failed: %s", job.id, exc)
    except Exception as exc:  # pragma: no cover
        job.status = ImportStatus.FAILED
        job.error = f"Unexpected error while inspecting file: {type(exc).__name__}"
        log.exception("Import %s inspection crashed", job.id)
    db.commit()
    db.refresh(job)
    return job


@router.get("", response_model=list[ImportJobOut])
def list_jobs(plant: PlantDep, db: DB, limit: int = Query(50, le=500)):
    return list(
        db.scalars(
            select(ImportJob)
            .where(ImportJob.plant_id == plant.id)
            .order_by(desc(ImportJob.created_at))
            .limit(limit)
        )
    )


@router.get("/{job_id}", response_model=ImportJobOut)
def get_job(job_id: int, plant: PlantDep, db: DB):
    return _job(db, plant, job_id)


@router.post("/{job_id}/preview")
def preview(job_id: int, payload: PreviewRequest, plant: PlantDep, db: DB) -> dict:
    """Validate a mapping, run quality checks and return a preview. Nothing is inserted."""
    job = _job(db, plant, job_id)
    if job.status == ImportStatus.COMMITTED:
        raise HTTPException(status.HTTP_409_CONFLICT, "This import has already been committed")
    path = Path(job.stored_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Uploaded file is no longer available")
    try:
        table = load_table(
            path, sheet_name=payload.sheet_name or job.sheet_name, header_row=payload.header_row
        )
        if payload.sheet_name and payload.sheet_name != job.sheet_name:
            job.inspection = clean_nan(profile_table(table))
            job.sheet_name = table.sheet_name
        data = normalize(table.df, payload.mapping, plant.timezone)
        if data.interval is not None:
            data.interval.attrs["tz"] = payload.mapping.timezone or plant.timezone
        report = clean_nan(run_quality_checks(data, plant.capacity_kw))
    except (MappingError, UnsupportedFileError, ValueError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    job.column_mapping = payload.mapping.model_dump()
    job.timezone = payload.mapping.timezone or plant.timezone
    job.quality_report = report
    job.rows_valid = int(len(data.interval)) if data.interval is not None else int(len(data.daily))
    job.rows_rejected = report.get("rejected_count", 0)
    job.status = ImportStatus.PREVIEWED
    job.error = None
    db.commit()
    preview_frame = data.interval if data.interval is not None else data.daily
    sample = preview_frame.head(20).copy()
    for c in sample.columns:
        if str(sample[c].dtype).startswith("datetime"):
            sample[c] = sample[c].astype(str)
    sample["date"] = sample["date"].astype(str) if "date" in sample else None
    return {
        "job_id": job.id,
        "kind": data.kind,
        "quality_report": report,
        "preview_rows": clean_nan(sample.where(sample.notna(), None).to_dict(orient="records")),
        "daily_preview": clean_nan(
            data.daily.assign(date=data.daily["date"].astype(str))
            .head(60)
            .where(data.daily.notna(), None)
            .to_dict(orient="records")
        )
        if data.daily is not None
        else [],
    }


@router.post("/{job_id}/commit", response_model=ImportJobOut)
def commit(job_id: int, plant: PlantDep, db: DB):
    job = _job(db, plant, job_id)
    if job.status != ImportStatus.PREVIEWED or not job.column_mapping:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Run a preview with a confirmed mapping before committing"
        )
    if job.quality_report and job.quality_report.get("status") == "fail":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Quality checks failed; fix the mapping or file first"
        )
    path = Path(job.stored_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Uploaded file is no longer available")
    try:
        mapping = ColumnMapping.model_validate(job.column_mapping)
        table = load_table(path, sheet_name=job.sheet_name)
        data = normalize(table.df, mapping, plant.timezone)
        if data.interval is not None:
            data.interval.attrs["tz"] = mapping.timezone or plant.timezone
        run_quality_checks(data, plant.capacity_kw)  # populates quality_flags on rows
        result = commit_import(db, data, plant_id=plant.id, import_job_id=job.id)
    except (MappingError, UnsupportedFileError, ValueError) as exc:
        db.rollback()
        job.status = ImportStatus.FAILED
        job.error = str(exc)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        db.rollback()
        job.status = ImportStatus.FAILED
        job.error = f"{type(exc).__name__}: {exc}"
        db.commit()
        log.exception("Import %s commit crashed", job.id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Import failed; see import job error"
        ) from exc
    job.status = ImportStatus.COMMITTED
    job.committed_at = datetime.now(UTC)
    job.rows_inserted = result["readings"]["inserted"] + result["daily"]["inserted"]
    job.rows_updated = result["readings"]["updated"] + result["daily"]["updated"]
    job.rows_duplicate = result["readings"]["updated"] + result["daily"].get("skipped", 0)
    job.quality_report = {**(job.quality_report or {}), "commit_result": result}
    db.commit()
    db.refresh(job)
    return job


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, plant: PlantDep, db: DB):
    """Delete an import job and its uploaded file (data already committed is kept)."""
    job = _job(db, plant, job_id)
    try:
        Path(job.stored_path).unlink(missing_ok=True)
    except OSError:
        pass
    db.delete(job)
    db.commit()
