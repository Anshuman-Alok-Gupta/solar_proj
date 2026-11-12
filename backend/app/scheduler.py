"""Background jobs: scheduled SEMS sync and weather sync (APScheduler, in-process)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _run_sems_sync() -> None:
    from app.db.session import SessionLocal
    from app.integrations.sems.sync import sync_all_sems_plants

    with SessionLocal() as db:
        try:
            sync_all_sems_plants(db, mode="incremental")
        except Exception:
            log.exception("Scheduled SEMS sync failed")


def _run_weather_sync() -> None:
    from app.db.session import SessionLocal
    from app.integrations.weather.sync import sync_weather_all_plants

    with SessionLocal() as db:
        try:
            sync_weather_all_plants(db)
        except Exception:
            log.exception("Scheduled weather sync failed")


def start_scheduler() -> None:
    global _scheduler
    settings = get_settings()
    if _scheduler is not None:
        return
    sched = BackgroundScheduler(timezone="UTC")
    if settings.sems_sync_enabled and settings.sems_configured:
        sched.add_job(
            _run_sems_sync,
            "interval",
            seconds=settings.sems_sync_interval_seconds,
            id="sems_sync",
            max_instances=1,
            coalesce=True,
        )
        log.info("SEMS sync scheduled every %ss", settings.sems_sync_interval_seconds)
    if settings.weather_sync_enabled:
        sched.add_job(
            _run_weather_sync,
            "interval",
            seconds=settings.weather_sync_interval_seconds,
            id="weather_sync",
            max_instances=1,
            coalesce=True,
        )
        log.info("Weather sync scheduled every %ss", settings.weather_sync_interval_seconds)
    sched.start()
    _scheduler = sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
