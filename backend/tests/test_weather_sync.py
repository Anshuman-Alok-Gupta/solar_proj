"""Weather sync: rate-limit handling, partial runs and forecast throttling (no network)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from tenacity import wait_none

from app.db.models import Plant, SyncStatus, WeatherDaily, WeatherKind
from app.integrations.weather import sync as wsync
from app.integrations.weather.open_meteo import (
    HOURLY_VARS,
    OpenMeteoClient,
    OpenMeteoRateLimitError,
    OpenMeteoTransientError,
    WeatherBundle,
)

DAILY_429 = {"error": True, "reason": "Daily API request limit exceeded. Please try again tomorrow."}
MINUTELY_429 = {
    "error": True,
    "reason": "Minutely API request limit exceeded. Please try again in one minute.",
}


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    monkeypatch.setattr(OpenMeteoClient._get.retry, "wait", wait_none())


def _client_returning(status: int, body: dict) -> tuple[OpenMeteoClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return OpenMeteoClient(client=httpx.Client(transport=httpx.MockTransport(handler))), seen


def test_daily_429_keeps_reason_and_is_not_retried():
    c, seen = _client_returning(429, DAILY_429)
    with pytest.raises(OpenMeteoRateLimitError, match="Daily API request limit exceeded"):
        c.forecast(18.5, 73.8, "Asia/Kolkata")
    assert len(seen) == 1


def test_minutely_429_is_retried():
    c, seen = _client_returning(429, MINUTELY_429)
    with pytest.raises(OpenMeteoTransientError, match="Minutely"):
        c.forecast(18.5, 73.8, "Asia/Kolkata")
    assert len(seen) == 4


def _bundle(start: date, end: date, source: str) -> WeatherBundle:
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    times = [datetime(d.year, d.month, d.day, h).isoformat() for d in days for h in range(24)]
    hourly = {"time": times, **{v: [100.0] * len(times) for v in HOURLY_VARS}}
    daily = {"time": [d.isoformat() for d in days], "shortwave_radiation_sum": [20.0] * len(days)}
    return WeatherBundle(source, "Asia/Kolkata", 19800, hourly, daily, {}, {})


class FakeClient:
    """Stands in for OpenMeteoClient; ``fail`` maps method name -> exception to raise."""

    fail: dict[str, Exception] = {}
    calls: list[str] = []

    def __init__(self, *a, **kw):
        pass

    def close(self):
        pass

    def _maybe_fail(self, name: str):
        FakeClient.calls.append(name)
        if name in FakeClient.fail:
            raise FakeClient.fail[name]

    def archive(self, lat, lon, tz, start, end, **kw):
        self._maybe_fail("archive")
        return _bundle(start, end, "open-meteo-era5")

    def historical_forecast(self, lat, lon, tz, start, end, **kw):
        self._maybe_fail("historical_forecast")
        return _bundle(start, end, "open-meteo-historical-forecast")

    def forecast(self, lat, lon, tz, *, forecast_days=7, **kw):
        self._maybe_fail("forecast")
        today = datetime.now(ZoneInfo(tz)).date()
        return _bundle(today, today + timedelta(days=forecast_days - 1), "open-meteo-forecast")


@pytest.fixture()
def fake(monkeypatch):
    FakeClient.fail = {}
    FakeClient.calls = []
    monkeypatch.setattr(wsync, "OpenMeteoClient", FakeClient)
    return FakeClient


def _run(db, plant_id: int):
    plant = db.get(Plant, plant_id)
    start = datetime.now(ZoneInfo(plant.timezone)).date() - timedelta(days=10)
    return wsync.run_weather_sync(db, plant, start=start)


def _count(db, plant_id: int, kind: WeatherKind) -> int:
    return db.query(WeatherDaily).filter_by(plant_id=plant_id, kind=kind).count()


def test_forecast_rate_limit_is_partial_and_keeps_observed(db, plant, fake):
    fake.fail = {"forecast": OpenMeteoRateLimitError("HTTP 429 from x: " + DAILY_429["reason"])}
    row = _run(db, plant["id"])
    assert row.status == SyncStatus.PARTIAL
    assert "forecast: HTTP 429" in row.error and "Daily API request limit" in row.error
    assert row.message.endswith("forecast failed")
    assert _count(db, plant["id"], WeatherKind.OBSERVED) > 0
    assert _count(db, plant["id"], WeatherKind.FORECAST) == 0


def test_fresh_forecast_is_not_refetched(db, plant, fake):
    first = _run(db, plant["id"])
    assert first.status == SyncStatus.SUCCESS
    assert fake.calls.count("forecast") == 1

    second = _run(db, plant["id"])
    assert second.status == SyncStatus.SUCCESS
    assert fake.calls.count("forecast") == 1  # skipped: stored forecast is younger than 3 h
    assert second.details["forecast"]["skipped"] is True
    assert "not re-fetched" in second.message


def test_observed_rate_limit_skips_forecast_request(db, plant, fake):
    fake.fail = {"archive": OpenMeteoRateLimitError("HTTP 429 from x: " + DAILY_429["reason"])}
    row = _run(db, plant["id"])
    assert row.status == SyncStatus.FAILED
    assert "forecast" not in fake.calls
    assert "observed: HTTP 429" in row.error
