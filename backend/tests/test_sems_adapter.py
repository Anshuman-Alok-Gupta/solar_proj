"""SEMS client + adapter tests with mocked HTTP (respx) - no network."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from app.db.models import DataSource
from app.integrations.sems.adapter import build_live_reading, flatten_factors, statistics_to_daily_rows
from app.integrations.sems.client import (
    NEW_LOGIN_URL,
    SemsApiError,
    SemsAuthError,
    SemsClient,
    SemsRateLimitError,
)

API = "https://hk-gateway.semsportal.com/web/sems"


def _login_ok(request):
    return httpx.Response(
        200,
        json={
            "code": 0,
            "msg": "ok",
            "data": {"uid": "u", "token": "t", "timestamp": 1, "client": "semsPlusWeb", "api": API},
        },
    )


@respx.mock
def test_login_web_and_station_listing():
    respx.post(NEW_LOGIN_URL).mock(side_effect=_login_ok)
    respx.post(f"{API}/sems-plant/api/portal/stations/page").mock(
        return_value=httpx.Response(
            200,
            json={"code": 0, "data": {"dataList": [{"id": "abc", "name": "Home", "installedPower": 6.0}]}},
        )
    )
    with SemsClient("a@b.c", "pw") as c:
        tok = c.login("web")
        assert tok.api == API and tok.data["client"] == "semsPlusWeb"
        stations = c.list_stations()
    assert stations[0]["id"] == "abc"
    sent = respx.calls[1].request
    assert "X-Signature" in sent.headers and "token" in sent.headers


@respx.mock
def test_login_rejected_raises_auth_error():
    respx.post(NEW_LOGIN_URL).mock(
        return_value=httpx.Response(200, json={"code": 100004, "msg": "parameter error."})
    )
    with SemsClient("a@b.c", "pw") as c, pytest.raises(SemsAuthError):
        c.login("web")


@respx.mock
def test_rate_limit_code_triggers_cooldown():
    respx.post(NEW_LOGIN_URL).mock(side_effect=_login_ok)
    respx.post(f"{API}/sems-plant/api/portal/stations/page").mock(
        return_value=httpx.Response(200, json={"code": "GY0429", "msg": "too many"})
    )
    with SemsClient("a@b.c", "pw") as c:
        with pytest.raises(SemsRateLimitError):
            c.list_stations()
        assert c.cooling_down_for > 0
        with pytest.raises(SemsRateLimitError):  # skipped locally while cooling down - no extra HTTP call
            c.list_stations()
    assert len([call for call in respx.calls if "stations/page" in str(call.request.url)]) == 1


@respx.mock
def test_expired_token_relogin_once():
    respx.post(NEW_LOGIN_URL).mock(side_effect=_login_ok)
    route = respx.post(f"{API}/sems-plant/api/portal/stations/page")
    route.side_effect = [
        httpx.Response(200, json={"code": "100002", "msg": "expired"}),
        httpx.Response(200, json={"code": 0, "data": {"dataList": []}}),
    ]
    with SemsClient("a@b.c", "pw") as c:
        assert c.list_stations() == []
    assert route.call_count == 2
    assert len([call for call in respx.calls if str(call.request.url) == NEW_LOGIN_URL]) == 2


@respx.mock
def test_api_error_surfaces_code():
    respx.post(NEW_LOGIN_URL).mock(side_effect=_login_ok)
    respx.get(url__startswith=f"{API}/sems-plant/api/stations/flow").mock(
        return_value=httpx.Response(200, json={"code": "E1", "msg": "nope"})
    )
    with SemsClient("a@b.c", "pw") as c, pytest.raises(SemsApiError) as exc:
        c.station_flow("abc")
    assert exc.value.code == "E1"


def test_flatten_and_live_reading():
    telemetry = [
        {
            "code": "ac",
            "factors": [
                {"code": "pAc", "data": "1.25"},
                {"code": "PHASE-A:Vac", "data": "238.1"},
                {"code": "Fac", "data": "50.01"},
                {"code": "PHASE-A:Iac", "data": "1.8"},
            ],
        },
        {
            "code": "pv",
            "factors": [
                {"code": "MPPT-1:Vpv", "data": "300"},
                {"code": "MPPT-2:Vpv", "data": "310"},
                {"code": "MPPT-1:Ipv", "data": "2.0"},
                {"code": "MPPT-2:Ipv", "data": "2.2"},
            ],
        },
        {"code": "system", "factors": [{"code": "Temperature", "data": "41"}]},
    ]
    counters = [
        {
            "code": "x",
            "factors": [
                {"code": "proPvStatsTotal", "data": 901.2},
                {"code": "proPvStatsToday", "data": 14.5},
                {"code": "ratedPower", "data": 6},
            ],
        }
    ]
    assert flatten_factors(telemetry)["pAc"] == "1.25"
    row = build_live_reading(
        plant_id=1,
        inverter_id=2,
        telemetry=telemetry,
        counters=counters,
        flow={"refreshTime": "2026-11-11T12:00:00"},
        device_status=1,
        tz_name="Asia/Kolkata",
        sync_log_id=None,
        previous_cumulative_kwh=900.0,
    )
    assert (
        row["power_w"] == 1250.0
        and row["pv_voltage_v"] == 305.0
        and row["pv_current_a"] == pytest.approx(4.2)
    )
    assert row["ac_voltage_v"] == 238.1 and row["frequency_hz"] == 50.01 and row["temperature_c"] == 41
    assert row["cumulative_energy_kwh"] == 901.2 and row["energy_kwh"] == pytest.approx(1.2)
    assert row["ts"].isoformat() == "2026-11-11T06:30:00+00:00"  # IST -> UTC
    assert row["_extra"]["eday_kwh"] == 14.5 and row["source"] == DataSource.SEMS_API


def test_standby_without_power_is_zero_with_flag():
    row = build_live_reading(
        plant_id=1,
        inverter_id=None,
        telemetry=[],
        counters=[],
        flow={},
        device_status=0,
        tz_name="UTC",
        sync_log_id=None,
    )
    assert row["power_w"] == 0.0 and "power_assumed_zero_standby" in row["quality_flags"]


def test_statistics_to_daily_rows_without_meter_nulls_consumption():
    stats = {
        "dataList": [
            {
                "item": "proSystemTotalStats",
                "statisticsList": [{"date": "2026-11-01", "val": 13.0}, {"date": "2026-11-02", "val": 4.8}],
            },
            {
                "item": "proConsumStats",
                "statisticsList": [{"date": "2026-11-01", "val": 13.0}, {"date": "2026-11-02", "val": 4.8}],
            },
        ]
    }
    rows = statistics_to_daily_rows(
        stats, plant_id=1, today_local=date(2026, 11, 2), has_meter=False, sync_log_id=None
    )
    assert [r["generation_kwh"] for r in rows] == [13.0, 4.8]
    assert all(r["consumption_kwh"] is None for r in rows)
    assert rows[0]["is_complete"] is True and rows[1]["is_complete"] is False
    assert (
        "partial_day" in rows[1]["quality_flags"]
        and "no_smart_meter_consumption_unmeasured" in rows[0]["quality_flags"]
    )
    rows_m = statistics_to_daily_rows(
        stats, plant_id=1, today_local=date(2026, 11, 3), has_meter=True, sync_log_id=None
    )
    assert rows_m[0]["consumption_kwh"] == 13.0
