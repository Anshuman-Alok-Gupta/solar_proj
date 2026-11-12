"""Open-Meteo client (no API key required).

Verified endpoints (2026-11-11):
* Forecast:   https://api.open-meteo.com/v1/forecast            (up to 16 days ahead, +past_days)
* Archive:    https://archive-api.open-meteo.com/v1/archive     (ERA5 reanalysis, ~5 day delay)
* Hist. fcst: https://historical-forecast-api.open-meteo.com/v1/forecast (archived forecasts, 2022+)

All hourly radiation variables are W/m^2 averaged over the preceding hour, so
summing hourly values gives Wh/m^2 per day. ``global_tilted_irradiance`` requires
``tilt`` and ``azimuth`` (0 = south, -90 = east, 90 = west).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"

HOURLY_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
    "direct_normal_irradiance",
    "global_tilted_irradiance",
]
DAILY_VARS = [
    "shortwave_radiation_sum",
    "sunshine_duration",
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "weather_code",
]
# cloud_cover_mean is only available on the forecast-family endpoints, not ERA5 archive
DAILY_VARS_FORECAST = DAILY_VARS + ["cloud_cover_mean"]


class OpenMeteoError(RuntimeError):
    pass


class OpenMeteoTransientError(OpenMeteoError):
    pass


class OpenMeteoRateLimitError(OpenMeteoError):
    """Hourly/daily quota exhausted (HTTP 429). Not retried: it won't clear within seconds.

    The free API is limited per *IP*, so on shared egress (e.g. Render free tier) other
    tenants' traffic counts against the same quota.
    """


def _reason(resp: httpx.Response) -> str | None:
    """Open-Meteo puts a human-readable ``reason`` in its JSON error bodies."""
    try:
        reason = resp.json().get("reason")
    except Exception:
        reason = resp.text[:200].strip() or None
    return str(reason) if reason else None


@dataclass(slots=True)
class WeatherBundle:
    """Raw hourly + daily arrays as returned by Open-Meteo, plus metadata."""

    source: str
    timezone: str
    utc_offset_seconds: int
    hourly: dict[str, list[Any]]
    daily: dict[str, list[Any]]
    hourly_units: dict[str, str]
    daily_units: dict[str, str]


class OpenMeteoClient:
    def __init__(self, timeout: float = 30.0, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            timeout=timeout, headers={"User-Agent": "solar-intel-platform/0.1"}
        )

    def close(self) -> None:
        self._client.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        retry=retry_if_exception_type(OpenMeteoTransientError),
    )
    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = self._client.get(url, params=params)
        except httpx.TransportError as exc:
            raise OpenMeteoTransientError(f"network error: {exc}") from exc
        if resp.status_code != 200:
            reason = _reason(resp)
            msg = f"HTTP {resp.status_code} from {url}" + (f": {reason}" if reason else "")
            if resp.status_code == 429:
                # Minutely limits clear quickly, so retry those; hourly/daily ones don't.
                if reason and any(w in reason.lower() for w in ("hourly", "daily")):
                    raise OpenMeteoRateLimitError(msg)
                raise OpenMeteoTransientError(msg)
            if resp.status_code >= 500:
                raise OpenMeteoTransientError(msg)
            raise OpenMeteoError(msg)
        data = resp.json()
        if data.get("error"):
            raise OpenMeteoError(str(data.get("reason")))
        return data

    @staticmethod
    def _base_params(
        lat: float, lon: float, tz: str, tilt: float | None, azimuth: float | None
    ) -> dict[str, Any]:
        p: dict[str, Any] = {
            "latitude": lat,
            "longitude": lon,
            "timezone": tz,
            "hourly": ",".join(HOURLY_VARS),
        }
        # Open-Meteo returns GTI only with tilt/azimuth; default to a horizontal-ish 20 deg south if unknown
        p["tilt"] = tilt if tilt is not None else 20
        p["azimuth"] = azimuth if azimuth is not None else 0
        return p

    @staticmethod
    def _bundle(data: dict[str, Any], source: str) -> WeatherBundle:
        return WeatherBundle(
            source=source,
            timezone=data.get("timezone", "UTC"),
            utc_offset_seconds=int(data.get("utc_offset_seconds", 0)),
            hourly=data.get("hourly", {}),
            daily=data.get("daily", {}),
            hourly_units=data.get("hourly_units", {}),
            daily_units=data.get("daily_units", {}),
        )

    def forecast(
        self,
        lat: float,
        lon: float,
        tz: str,
        *,
        forecast_days: int = 7,
        past_days: int = 0,
        tilt: float | None = None,
        azimuth: float | None = None,
    ) -> WeatherBundle:
        params = self._base_params(lat, lon, tz, tilt, azimuth)
        params.update(
            {
                "daily": ",".join(DAILY_VARS_FORECAST),
                "forecast_days": max(1, min(forecast_days, 16)),
                "past_days": max(0, min(past_days, 92)),
            }
        )
        return self._bundle(self._get(FORECAST_URL, params), "open-meteo-forecast")

    def archive(
        self,
        lat: float,
        lon: float,
        tz: str,
        start: date,
        end: date,
        *,
        tilt: float | None = None,
        azimuth: float | None = None,
    ) -> WeatherBundle:
        params = self._base_params(lat, lon, tz, tilt, azimuth)
        params.update(
            {
                "daily": ",".join(DAILY_VARS),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        )
        return self._bundle(self._get(ARCHIVE_URL, params), "open-meteo-era5")

    def historical_forecast(
        self,
        lat: float,
        lon: float,
        tz: str,
        start: date,
        end: date,
        *,
        tilt: float | None = None,
        azimuth: float | None = None,
    ) -> WeatherBundle:
        """Archived *forecasts* (what a forecast said), used to fill recent days ERA5 has not reached."""
        params = self._base_params(lat, lon, tz, tilt, azimuth)
        params.update(
            {
                "daily": ",".join(DAILY_VARS_FORECAST),
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            }
        )
        return self._bundle(self._get(HISTORICAL_FORECAST_URL, params), "open-meteo-historical-forecast")
