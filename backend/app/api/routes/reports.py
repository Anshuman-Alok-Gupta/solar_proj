from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, select

from app.api.deps import DB, PlantDep
from app.db.models import Report, ReportKind
from app.services.reports import build_report, report_csv, save_report

router = APIRouter(prefix="/plants/{plant_id}/reports", tags=["reports"])


def _out(r: Report) -> dict:
    return {
        "id": r.id,
        "kind": r.kind.value,
        "period_start": r.period_start.isoformat(),
        "period_end": r.period_end.isoformat(),
        "title": r.title,
        "summary_text": r.summary_text,
        "content": r.content,
        "created_at": r.created_at.isoformat(),
    }


@router.post("/generate")
def generate(
    plant: PlantDep,
    db: DB,
    kind: ReportKind = Query(ReportKind.WEEKLY),
    anchor: date | None = None,
    include_synthetic: bool = False,
) -> dict:
    built = build_report(db, plant, kind, anchor, include_synthetic=include_synthetic)
    if built["content"]["period"]["days_with_data"] == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No data for the {kind.value} period starting {built['period_start']}",
        )
    return _out(save_report(db, plant, built, kind))


@router.get("/preview")
def preview(
    plant: PlantDep,
    db: DB,
    kind: ReportKind = Query(ReportKind.WEEKLY),
    anchor: date | None = None,
    include_synthetic: bool = False,
) -> dict:
    built = build_report(db, plant, kind, anchor, include_synthetic=include_synthetic)
    return {
        "title": built["title"],
        "period_start": built["period_start"].isoformat(),
        "period_end": built["period_end"].isoformat(),
        "summary_text": built["summary_text"],
        "content": built["content"],
    }


@router.get("")
def list_reports(plant: PlantDep, db: DB, limit: int = Query(50, le=500)) -> dict:
    rows = db.scalars(
        select(Report).where(Report.plant_id == plant.id).order_by(desc(Report.created_at)).limit(limit)
    ).all()
    return {"plant_id": plant.id, "items": [_out(r) for r in rows]}


@router.get("/{report_id}")
def get_report(report_id: int, plant: PlantDep, db: DB) -> dict:
    r = db.get(Report, report_id)
    if r is None or r.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return _out(r)


@router.get("/{report_id}/csv", response_class=PlainTextResponse)
def get_report_csv(report_id: int, plant: PlantDep, db: DB):
    r = db.get(Report, report_id)
    if r is None or r.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return PlainTextResponse(
        report_csv(r),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="report_{r.id}.csv"'},
    )


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(report_id: int, plant: PlantDep, db: DB):
    r = db.get(Report, report_id)
    if r is None or r.plant_id != plant.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    db.delete(r)
    db.commit()
