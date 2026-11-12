"""GoodWe SEMS portal client.

IMPORTANT - what this talks to
------------------------------
GoodWe officially offers three APIs (OpenAPI for *organisation* accounts, a licensed
real-time data API, and a Kafka remote-control interface). None of them is available
to a normal household SEMS account. This client therefore speaks the same undocumented
HTTPS API that the official SEMS+ web portal / app use, following the protocol that the
open-source Home Assistant (TimSoethout/goodwe-sems-home-assistant), pygoodwe and
ioBroker integrations implement (verified against their source on 2026-11-11):

* "new"    login:  POST https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login
                   body {account, pwd: base64(md5hex(password)), agreement:1, isChinese:false, isLocal:false}
* "web"    login:  same endpoint with SEMS+ web headers + X-Signature; the resulting token is
                   required for the /sems-plant/api/* endpoints.
* "legacy" login:  POST https://www.semsportal.com/api/v3/Common/CrossLogin (plain password)

Data endpoints (web token, base = api url from login, e.g. https://eu-gateway.semsportal.com/web/sems):
    POST /sems-plant/api/portal/stations/page            {current, size}                -> dataList[{id,...}]
    GET  /sems-plant/api/stations/device/all-status?stationId=                             -> deviceDetailList[...]
    GET  /sems-plant/api/stations/flow?stationId=                                          -> power flow
    GET  /sems-plant/api/equipments/{sn}/telemetry?deviceType=INVERTER&pwId=               -> factor groups
    GET  /sems-plant/api/equipments/{sn}/telecounting?deviceType=INVERTER&pwId=            -> energy counters
    POST /sems-plant/api/stations/statistics  {stationId,isReport,items,dimension,startTime,endTime}
    POST /sems-plant/api/stations/production  {stationId,items,dimension,isReport,startTime,endTime}
Legacy (new/legacy token, base rewritten to https://{region}.semsportal.com/api):
    POST /v2/PowerStation/GetMonitorDetailByPowerstationId {powerStationId}

Behaviour rules: no scraping, no bypassing; polite polling only (>= 5 min), honour HTTP 429 and
the GY0429 rate-limit code with a cool-down, re-login at most once per call on an expired token.
GoodWe may change this API at any time - every response is validated defensively.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import httpx

log = logging.getLogger(__name__)

LoginMode = Literal["new", "web", "legacy"]

LEGACY_LOGIN_URL = "https://www.semsportal.com/api/v3/Common/CrossLogin"
NEW_LOGIN_URL = "https://semsplus.goodwe.com/web/sems/sems-user/api/v1/auth/cross-login"
NEW_LOGIN_FALLBACK_API = "https://eu-gateway.semsportal.com/web/sems"
LEGACY_API_FALLBACK = "https://eu.semsportal.com/api"

SUCCESS_CODES = {0, "0", "00000"}
RATE_LIMIT_CODE = "GY0429"
AUTH_EXPIRED_CODES = {"100002", "C0602"}
TRANSIENT_LOGIN_CODES = {"C0602", RATE_LIMIT_CODE}

REQUEST_TIMEOUT = 30.0
COOLDOWN_BASE_SECONDS = 60
COOLDOWN_MAX_SECONDS = 900

_DEFAULT_UA = "solar-intel-platform/0.1 (personal household monitoring)"
_WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

STATISTICS_ITEMS = [
    "proConsumStats",
    "proGridStats",
    "proPurchaseStats",
    "proSelfConsumStats",
    "proSystemTotalStats",
    "proCharStats",
    "proDischarStats",
]
PRODUCTION_ITEMS = [
    "proConsumStats",
    "proGridStats",
    "proPurchaseStats",
    "profitGridStats",
    "profitProStats",
    "proSystemTotalStats",
]


class SemsError(RuntimeError):
    """Base error."""


class SemsAuthError(SemsError):
    """Credentials rejected."""


class SemsAuthExpiredError(SemsError):
    """Token expired / replaced (re-login required)."""


class SemsRateLimitError(SemsError):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


class SemsNetworkError(SemsError):
    pass


class SemsApiError(SemsError):
    def __init__(self, message: str, code: Any = None, payload: Any = None):
        super().__init__(message)
        self.code = code
        self.payload = payload


@dataclass
class SemsToken:
    mode: LoginMode
    api: str
    data: dict[str, Any] = field(default_factory=dict)
    obtained_at: float = field(default_factory=time.time)


class SemsClient:
    def __init__(
        self,
        account: str,
        password: str,
        *,
        timeout: float = REQUEST_TIMEOUT,
        client: httpx.Client | None = None,
        preferred_mode: LoginMode = "new",
    ) -> None:
        if not account or not password:
            raise SemsAuthError("SEMS account and password are required")
        self._account = account
        self._password = password
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._tokens: dict[LoginMode, SemsToken] = {}
        self._lock = threading.RLock()
        self._cooldown_until: float = 0.0
        self._cooldown_seconds: int = COOLDOWN_BASE_SECONDS
        self._preferred_mode: LoginMode = preferred_mode
        self.last_login_error: str | None = None

    # ------------------------------------------------------------------ helpers

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SemsClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def cooling_down_for(self) -> int:
        return max(0, int(self._cooldown_until - time.time()))

    def _start_cooldown(self, retry_after: int | None = None) -> int:
        secs = retry_after if retry_after else self._cooldown_seconds
        secs = min(secs, 3600)
        self._cooldown_until = time.time() + secs
        self._cooldown_seconds = min(self._cooldown_seconds * 2, COOLDOWN_MAX_SECONDS)
        return secs

    def _reset_cooldown(self) -> None:
        self._cooldown_seconds = COOLDOWN_BASE_SECONDS
        self._cooldown_until = 0.0

    def _raise_if_cooling_down(self, op: str) -> None:
        remaining = self.cooling_down_for
        if remaining > 0:
            raise SemsRateLimitError(f"{op} skipped: SEMS client cooling down for {remaining}s", remaining)

    @staticmethod
    def _hash_password(password: str) -> str:
        md5_hex = hashlib.md5(password.encode("utf-8"), usedforsecurity=False).hexdigest()
        return base64.b64encode(md5_hex.encode("utf-8")).decode("utf-8")

    @staticmethod
    def _signature(uid: str = "", token: str = "") -> str:
        epoch_ms = round(time.time() * 1000)
        digest = hashlib.sha256(f"{epoch_ms}@{uid}@{token}".encode()).hexdigest()
        return base64.b64encode(f"{digest}@{epoch_ms}".encode()).decode()

    @staticmethod
    def redact(obj: Any) -> Any:
        """Redact token-like fields for logging / API exposure."""
        if isinstance(obj, dict):
            return {
                k: (
                    "***"
                    if k.lower() in {"token", "pwd", "password", "uid", "account"}
                    else SemsClient.redact(v)
                )
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [SemsClient.redact(v) for v in obj]
        return obj

    # ------------------------------------------------------------------ HTTP

    def _http(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        *,
        json_body: Any | None = None,
        op: str,
    ) -> dict[str, Any]:
        self._raise_if_cooling_down(op)
        try:
            resp = self._client.request(method, url, headers=headers, json=json_body)
        except httpx.TransportError as exc:
            raise SemsNetworkError(f"{op}: network error: {exc}") from exc
        if resp.status_code == 429:
            retry_after = None
            try:
                retry_after = int(resp.headers.get("Retry-After", "")) or None
            except ValueError:
                retry_after = None
            secs = self._start_cooldown(retry_after)
            raise SemsRateLimitError(f"{op}: HTTP 429 from SEMS, pausing {secs}s", secs)
        if resp.status_code >= 500:
            raise SemsNetworkError(f"{op}: HTTP {resp.status_code} from SEMS")
        if resp.status_code in (401, 403):
            raise SemsAuthExpiredError(f"{op}: HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise SemsApiError(
                f"{op}: non-JSON response (HTTP {resp.status_code})", payload=resp.text[:300]
            ) from exc
        if not isinstance(payload, dict):
            raise SemsApiError(f"{op}: unexpected payload type {type(payload).__name__}", payload=payload)
        return payload

    # ------------------------------------------------------------------ login

    def _login_headers(self, mode: LoginMode) -> dict[str, str]:
        if mode == "legacy":
            return {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": _DEFAULT_UA,
                "token": json.dumps({"version": "3.1.1", "client": "ios", "language": "en"}),
            }
        if mode == "web":
            return {
                "Content-Type": "application/json",
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://semsplus.goodwe.com",
                "Referer": "https://semsplus.goodwe.com/",
                "User-Agent": _WEB_UA,
                "Token": json.dumps(
                    {
                        "uid": "",
                        "timestamp": 0,
                        "token": "",
                        "client": "semsPlusWeb",
                        "version": "",
                        "language": "en",
                    }
                ),
                "X-Signature": self._signature(),
            }
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, */*;q=0.5",
            "User-Agent": _DEFAULT_UA,
        }

    def login(self, mode: LoginMode) -> SemsToken:
        with self._lock:
            self._raise_if_cooling_down(f"{mode} login")
            op = f"SEMS {mode} login"
            if mode == "legacy":
                body = {"account": self._account, "pwd": self._password}
            else:
                body = {
                    "account": self._account,
                    "pwd": self._hash_password(self._password),
                    "agreement": 1,
                    "isChinese": False,
                    "isLocal": False,
                }
            url = LEGACY_LOGIN_URL if mode == "legacy" else NEW_LOGIN_URL
            payload = self._http("POST", url, self._login_headers(mode), json_body=body, op=op)
            code = payload.get("code")
            if code not in SUCCESS_CODES:
                msg = payload.get("msg") or payload.get("description") or "login rejected"
                self.last_login_error = f"{code}: {msg}"
                if str(code) == RATE_LIMIT_CODE:
                    secs = self._start_cooldown()
                    raise SemsRateLimitError(f"{op}: rate limited ({msg}); pausing {secs}s", secs)
                if str(code) in TRANSIENT_LOGIN_CODES:
                    raise SemsNetworkError(f"{op}: transient failure {code}: {msg}")
                raise SemsAuthError(f"{op}: rejected (code {code}: {msg})")
            data = payload.get("data")
            if not isinstance(data, dict) or not data.get("token"):
                raise SemsApiError(f"{op}: response missing token", code=code, payload=self.redact(payload))
            api = payload.get("api") if isinstance(payload.get("api"), str) else data.get("api")
            if not api:
                api = LEGACY_API_FALLBACK if mode == "legacy" else NEW_LOGIN_FALLBACK_API
            token_data = dict(data)
            token_data["api"] = api
            if mode == "web":
                token_data.setdefault("client", "semsPlusWeb")
            token = SemsToken(mode=mode, api=api, data=token_data)
            self._tokens[mode] = token
            self._reset_cooldown()
            self.last_login_error = None
            log.info("SEMS %s login ok (api=%s)", mode, api)
            return token

    def _token(self, mode: LoginMode, force: bool = False) -> SemsToken:
        with self._lock:
            tok = self._tokens.get(mode)
            if tok is None or force:
                tok = self.login(mode)
            return tok

    def _auth_headers(self, tok: SemsToken) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _WEB_UA if tok.mode == "web" else _DEFAULT_UA,
            "token": json.dumps(tok.data),
        }
        if tok.mode == "web":
            headers["X-Signature"] = self._signature(
                str(tok.data.get("uid", "")), str(tok.data.get("token", ""))
            )
            headers["Origin"] = "https://semsplus.goodwe.com"
            headers["Referer"] = "https://semsplus.goodwe.com/"
        return headers

    @staticmethod
    def _legacy_base(api: str) -> str:
        """Rewrite a gateway base to the regional legacy PowerStation host."""
        if "/web/sems" not in api and "/sems/" not in api:
            return api.rstrip("/")
        host = api.split("//", 1)[-1].split("/", 1)[0]
        region = None
        if host.endswith("-gateway.semsportal.com"):
            region = host.removesuffix("-gateway.semsportal.com") or None
        elif host.endswith(".semsportal.com"):
            region = host.split(".", 1)[0] or None
        return f"https://{region}.semsportal.com/api" if region else LEGACY_API_FALLBACK

    # ------------------------------------------------------------------ generic call

    def call(
        self,
        url_part: str,
        *,
        mode: LoginMode = "web",
        method: str = "POST",
        json_body: Any | None = None,
        op: str | None = None,
    ) -> Any:
        """Authenticated call; returns the ``data`` field. Re-logs in once on an expired token."""
        op = op or url_part
        for attempt in (1, 2):
            tok = self._token(mode, force=(attempt == 2))
            base = tok.api
            if url_part.startswith("/v2/PowerStation") or url_part.startswith("/PowerStation"):
                base = self._legacy_base(base)
            url = base.rstrip("/") + url_part
            try:
                payload = self._http(method, url, self._auth_headers(tok), json_body=json_body, op=op)
            except SemsAuthExpiredError:
                if attempt == 1:
                    continue
                raise
            code = payload.get("code")
            if code in SUCCESS_CODES:
                return payload.get("data")
            scode = str(code)
            if scode == RATE_LIMIT_CODE:
                secs = self._start_cooldown()
                raise SemsRateLimitError(f"{op}: SEMS rate limit {RATE_LIMIT_CODE}; pausing {secs}s", secs)
            if scode in AUTH_EXPIRED_CODES and attempt == 1:
                log.info("SEMS token expired (code %s) during %s; re-authenticating", code, op)
                continue
            raise SemsApiError(
                f"{op}: SEMS error {code}: {payload.get('msg') or payload.get('description')}",
                code=code,
                payload=self.redact(payload),
            )
        raise SemsApiError(f"{op}: exhausted retries")  # pragma: no cover

    # ------------------------------------------------------------------ endpoints (web)

    def list_stations(self, page: int = 1, size: int = 100) -> list[dict[str, Any]]:
        data = self.call(
            "/sems-plant/api/portal/stations/page",
            json_body={"current": page, "size": size},
            op="list stations",
        )
        if isinstance(data, dict):
            items = data.get("dataList") or data.get("records") or data.get("list") or []
            return [s for s in items if isinstance(s, dict)]
        return []

    def station_devices(self, station_id: str) -> list[dict[str, Any]]:
        data = self.call(
            f"/sems-plant/api/stations/device/all-status?stationId={station_id}",
            method="GET",
            op="station devices",
        )
        devices: list[dict[str, Any]] = []
        groups = data.get("deviceDetailList", []) if isinstance(data, dict) else []
        for group in groups:
            if not isinstance(group, dict):
                continue
            device_type = group.get("deviceType")
            for status_group in group.get("statusDetailList", []) or []:
                if not isinstance(status_group, dict):
                    continue
                detail_map = status_group.get("detailMap") or {}
                for sn in status_group.get("snList", []) or []:
                    detail = detail_map.get(sn, {}) if isinstance(detail_map, dict) else {}
                    devices.append(
                        {
                            **(detail if isinstance(detail, dict) else {}),
                            "sn": sn,
                            "deviceType": device_type,
                            "status": status_group.get("status"),
                        }
                    )
        return devices

    def station_flow(self, station_id: str) -> dict[str, Any]:
        data = self.call(
            f"/sems-plant/api/stations/flow?stationId={station_id}", method="GET", op="station flow"
        )
        return data if isinstance(data, dict) else {}

    def device_telemetry(self, station_id: str, sn: str, device_type: str = "INVERTER") -> Any:
        return self.call(
            f"/sems-plant/api/equipments/{sn}/telemetry?deviceType={device_type}&pwId={station_id}",
            method="GET",
            op="device telemetry",
        )

    def device_telecounting(self, station_id: str, sn: str, device_type: str = "INVERTER") -> Any:
        return self.call(
            f"/sems-plant/api/equipments/{sn}/telecounting?deviceType={device_type}&pwId={station_id}",
            method="GET",
            op="device counters",
        )

    def station_statistics(
        self,
        station_id: str,
        dimension: Literal["day", "month", "year"],
        start: datetime,
        end: datetime,
        items: list[str] | None = None,
    ) -> dict[str, Any]:
        data = self.call(
            "/sems-plant/api/stations/statistics",
            json_body={
                "stationId": station_id,
                "isReport": False,
                "items": items or STATISTICS_ITEMS,
                "dimension": dimension,
                "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
            },
            op=f"station statistics ({dimension})",
        )
        return data if isinstance(data, dict) else {}

    def station_production(self, station_id: str, start: datetime, end: datetime) -> dict[str, Any]:
        data = self.call(
            "/sems-plant/api/stations/production",
            json_body={
                "stationId": station_id,
                "items": PRODUCTION_ITEMS,
                "dimension": "day",
                "isReport": False,
                "startTime": start.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": end.strftime("%Y-%m-%d %H:%M:%S"),
            },
            op="station production",
        )
        return data if isinstance(data, dict) else {}

    def power_curve(self, station_id: str, day: date, tz_offset_hours: float) -> list[dict[str, Any]]:
        """One-minute PV power (kW) for a past or current day: ``[{"tp": "YYYY-MM-DD HH:MM:SS", "power": kW|None}]``.

        Same call the SEMS+ web portal makes for its daily power chart; ``timeZone`` is the
        station's UTC offset with the sign flipped (UTC+5:30 -> -5.5). Times are station-local.
        """
        data = self.call(
            "/sems-plant/api/v1/hems/power/statisticsAndPreV2",
            json_body={
                "stationId": station_id,
                "items": ["pSystem"],
                "timeScale": 1,
                "timeZone": -tz_offset_hours,
                "startTime": f"{day.isoformat()} 00:00:00",
                "endTime": f"{day.isoformat()} 23:59:59",
            },
            op="power curve",
        )
        for series in (data or {}).get("dataList", []) if isinstance(data, dict) else []:
            if isinstance(series, dict) and series.get("item") == "pSystem":
                return [p for p in series.get("powerData") or [] if isinstance(p, dict)]
        return []

    # ------------------------------------------------------------------ endpoints (legacy)

    def monitor_detail(self, station_id: str) -> dict[str, Any]:
        """Legacy rich snapshot (kpi, inverters with pac/eday/etotal, info)."""
        data = self.call(
            "/v2/PowerStation/GetMonitorDetailByPowerstationId",
            mode="legacy",
            json_body={"powerStationId": station_id},
            op="monitor detail",
        )
        return data if isinstance(data, dict) else {}

    def legacy_power_chart(self, station_id: str, day: datetime) -> Any:
        """Legacy per-day power curve (5-minute resolution in the portal).

        Endpoint used by pygoodwe / older SEMS apps. Treated as optional: if it is not
        available for this account the sync falls back to daily statistics only.
        """
        return self.call(
            "/v2/Charts/GetPlantPowerChart",
            mode="legacy",
            json_body={"id": station_id, "date": day.strftime("%Y-%m-%d"), "full_script": False},
            op="legacy power chart",
        )
