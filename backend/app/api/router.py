from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import (
    ai,
    analytics,
    auth,
    bills,
    ingestion,
    ml,
    plants,
    reports,
    sems,
    sharing,
    system,
    weather,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(plants.router)
api_router.include_router(weather.router)
api_router.include_router(sems.router)
api_router.include_router(ingestion.router)
api_router.include_router(analytics.router)
api_router.include_router(ml.router)
api_router.include_router(reports.router)
api_router.include_router(ai.router)
api_router.include_router(sharing.router)
api_router.include_router(bills.router)
