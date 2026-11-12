"""Background jobs: scheduled SEMS sync and weather sync (APScheduler, in-process)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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


def _run_ml_nightly() -> None:
    from app.db.session import SessionLocal
    from app.ml.nightly import run_nightly_all

    with SessionLocal() as db:
        try:
            for row in run_nightly_all(db):
                log.info("Nightly ML for plant %s: %s %s", row.plant_id, row.status.value, row.message)
        except Exception:
            log.exception("Nightly ML job failed")


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
            next_run_time=datetime.now(UTC) + timedelta(seconds=90),
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
            next_run_time=datetime.now(UTC) + timedelta(seconds=45),
            max_instances=1,
            coalesce=True,
        )
        log.info("Weather sync scheduled every %ss", settings.weather_sync_interval_seconds)
    if settings.ml_nightly_enabled:
        sched.add_job(
            _run_ml_nightly,
            "cron",
            hour=settings.ml_nightly_hour,
            minute=0,
            timezone=settings.ml_nightly_timezone,
            id="ml_nightly",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3 * 3600,  # still runs if the instance was busy/asleep at the hour
        )
        log.info("Nightly ML scheduled at %02d:00 %s", settings.ml_nightly_hour, settings.ml_nightly_timezone)
    sched.start()
    _scheduler = sched


def next_run(job_id: str):
    """Next scheduled run of a job (None when the scheduler or job is not running)."""
    job = _scheduler.get_job(job_id) if _scheduler is not None else None
    return job.next_run_time if job is not None else None


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
