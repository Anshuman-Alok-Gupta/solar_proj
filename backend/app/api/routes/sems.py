from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import desc, select

from app.api.deps import DB, PlantDep
from app.core.config import get_settings
from app.db.models import SyncLog
from app.integrations.sems.client import SemsError
from app.integrations.sems.sync import make_client, run_sems_sync, sems_status
from app.schemas.common import SyncLogOut

router = APIRouter(prefix="/plants/{plant_id}/sems", tags=["sems"])


def _require_configured() -> None:
    if not get_settings().sems_configured:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "SEMS_ACCOUNT / SEMS_PASSWORD are not configured on the server (.env)",
        )


@router.get("/status")
def get_status(plant: PlantDep, db: DB) -> dict:
    return sems_status(db, plant)


@router.post("/test-connection")
def test_connection(plant: PlantDep) -> dict:
    """Log in and list stations/devices. Returns masked identifiers only."""
    _require_configured()
    try:
        with make_client() as client:
            client.login("web")
            stations = client.list_stations()
            out = []
            for st in stations:
                sid = str(st.get("id", ""))
                devices = client.station_devices(sid) if sid else []
                out.append(
                    {
                        "station_id_masked": sid[:4] + "…" + sid[-4:],
                        "name": st.get("name"),
                        "installed_power_kw": st.get("installedPower"),
                        "production_total_kwh": st.get("productionTotal"),
                        "production_today_kwh": st.get("productionToday"),
                        "time_zone": st.get("timeZone"),
                        "create_time": st.get("createTime"),
                        "devices": [
                            {
                                "type": d.get("deviceType"),
                                "model": d.get("model"),
                                "name": d.get("name"),
                                "sn_masked": "…" + str(d.get("sn"))[-4:],
                                "status": d.get("status"),
                            }
                            for d in devices
                        ],
                    }
                )
            return {"ok": True, "login_mode": "web", "stations": out}
    except SemsError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"SEMS connection failed: {exc}") from exc


@router.post("/sync", response_model=SyncLogOut)
def trigger_sync(
    plant: PlantDep, db: DB, mode: str = Query("incremental", pattern="^(incremental|full|snapshot|curves)$")
):
    _require_configured()
    return run_sems_sync(db, plant, mode=mode)


@router.get("/sync-logs", response_model=list[SyncLogOut])
def sync_logs(plant: PlantDep, db: DB, limit: int = Query(20, le=200)):
    return list(
        db.scalars(
            select(SyncLog)
            .where(SyncLog.plant_id == plant.id, SyncLog.source == "sems")
            .order_by(desc(SyncLog.started_at))
            .limit(limit)
        )
    )
