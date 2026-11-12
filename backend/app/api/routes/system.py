from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DB
from app.core.config import get_settings

router = APIRouter(tags=["system"])


@router.get("/health")
def health(db: DB) -> dict:
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "time": datetime.now(UTC).isoformat()}


@router.get("/system/capabilities")
def capabilities() -> dict:
    """Which optional integrations are configured (never returns secret values)."""
    s = get_settings()
    return {
        "sems_configured": s.sems_configured,
        "sems_sync_enabled": s.sems_sync_enabled and s.sems_configured,
        "sems_sync_interval_seconds": s.sems_sync_interval_seconds,
        "weather_sync_enabled": s.weather_sync_enabled,
        "llm_configured": s.llm_configured,
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model_name if s.llm_configured else None,
        "app_env": s.app_env,
    }
