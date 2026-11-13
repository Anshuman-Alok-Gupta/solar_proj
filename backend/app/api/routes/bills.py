"""Electricity bills: read a PDF (locally, no AI), store the numbers, show measured savings.

The uploaded PDF is held in memory only while it is read, then discarded; its filename is
ignored (MSEDCL e-bill filenames contain the consumer number). Bills are not exposed to the AI
analyst.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from sqlalchemy import select

from app.api.deps import DB, PlantDep
from app.db.models import UtilityBill
from app.integrations.msedcl.bill_pdf import BillParseError, iso, parse_bill
from app.schemas.bill import BillIn
from app.services.bills import analyse, bills_overview

router = APIRouter(prefix="/plants/{plant_id}/bills", tags=["bills"])

MAX_BILL_BYTES = 5 * 1024 * 1024
FIELDS = [c for c in BillIn.model_fields if c != "tariff"] + ["tariff"]


def _out(b: UtilityBill, analysis: dict[str, Any] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"id": b.id}
    for k in FIELDS:
        v = getattr(b, k)
        d[k] = v.isoformat() if isinstance(v, date) else v
    d["updated_at"] = b.updated_at.isoformat() if b.updated_at else None
    if analysis is not None:
        d["analysis"] = analysis
    return d


def _get(db, plant_id: int, bill_id: int) -> UtilityBill:
    b = db.get(UtilityBill, bill_id)
    if b is None or b.plant_id != plant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bill not found")
    return b


def _apply(b: UtilityBill, data: BillIn) -> None:
    payload = data.model_dump()
    for k in FIELDS:
        setattr(b, k, payload[k])


@router.get("")
def list_bills(plant: PlantDep, db: DB) -> dict:
    ov = bills_overview(db, plant)
    return {"bills": [_out(b, a) for b, a in reversed(ov["bills"])], "summary": ov["summary"]}


@router.post("/parse")
async def parse(plant: PlantDep, db: DB, file: UploadFile = File(...)) -> dict:
    """Read the numbers from an MSEDCL e-bill PDF for review. Nothing is saved."""
    content = await file.read(MAX_BILL_BYTES + 1)
    await file.close()
    if len(content) > MAX_BILL_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "The bill PDF must be under 5 MB")
    try:
        res = parse_bill(content)
    except BillParseError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        del content
    fields = res["fields"]
    existing = None
    if fields.get("bill_month"):
        existing = db.scalar(
            select(UtilityBill.id).where(
                UtilityBill.plant_id == plant.id, UtilityBill.bill_month == fields["bill_month"]
            )
        )
    return {
        "fields": {**iso(fields), "source": "pdf"},
        "warnings": res["warnings"],
        "existing_bill_id": existing,
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def save_bill(plant: PlantDep, db: DB, data: BillIn) -> dict:
    """Create the bill, or replace the one already saved for the same billing month."""
    b = db.scalar(
        select(UtilityBill).where(UtilityBill.plant_id == plant.id, UtilityBill.bill_month == data.bill_month)
    )
    if b is None:
        b = UtilityBill(plant_id=plant.id)
        db.add(b)
    _apply(b, data)
    db.commit()
    db.refresh(b)
    return _out(b, analyse(db, plant, b))


@router.put("/{bill_id}")
def update_bill(plant: PlantDep, db: DB, bill_id: int, data: BillIn) -> dict:
    b = _get(db, plant.id, bill_id)
    clash = db.scalar(
        select(UtilityBill.id).where(
            UtilityBill.plant_id == plant.id,
            UtilityBill.bill_month == data.bill_month,
            UtilityBill.id != b.id,
        )
    )
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "Another bill is already saved for that month")
    _apply(b, data)
    db.commit()
    db.refresh(b)
    return _out(b, analyse(db, plant, b))


@router.delete("/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bill(plant: PlantDep, db: DB, bill_id: int) -> Response:
    db.delete(_get(db, plant.id, bill_id))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
