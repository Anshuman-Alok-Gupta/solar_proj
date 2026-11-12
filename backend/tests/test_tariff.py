"""Slab tariffs under net metering (MSEB-style)."""

from __future__ import annotations

import pytest

from app.services.tariff import marginal_rate, month_value, slab_cost
from tests.conftest import seed_days

MSEB = [
    {"up_to_kwh": 100, "rate": 4.28},
    {"up_to_kwh": 300, "rate": 11.10},
    {"up_to_kwh": 500, "rate": 15.38},
    {"up_to_kwh": 1000, "rate": 17.68},
    {"up_to_kwh": None, "rate": 17.68},
]


def test_slab_cost_walks_the_ladder():
    assert slab_cost(0, MSEB) == 0
    assert slab_cost(80, MSEB) == pytest.approx(80 * 4.28)
    assert slab_cost(400, MSEB) == pytest.approx(100 * 4.28 + 200 * 11.10 + 100 * 15.38)  # 4186
    assert slab_cost(1200, MSEB) == pytest.approx(428 + 2220 + 3076 + 500 * 17.68 + 200 * 17.68)
    assert marginal_rate(400, MSEB) == 15.38 and marginal_rate(250, MSEB) == 11.10


def test_generation_comes_off_the_top_slabs_and_surplus_is_settled():
    # 200 units offset of a 400-unit household: 100 @ 15.38 + 100 @ 11.10
    v = month_value(200, 400, MSEB, 2.82, coverage=1.0)
    assert v["bill_saving"] == pytest.approx(1538 + 1110)
    assert v["surplus_kwh"] == 0
    # 450 units: whole 400-unit bill cancelled, 50 units surplus at the settlement rate
    v = month_value(450, 400, MSEB, 2.82, coverage=1.0)
    assert v["bill_saving"] == pytest.approx(4186)
    assert v["surplus_kwh"] == pytest.approx(50) and v["value"] == pytest.approx(4186 + 50 * 2.82)
    # a half month is scaled to a full month for the slabs and back
    assert month_value(225, 400, MSEB, 2.82, coverage=0.5)["value"] == pytest.approx((4186 + 141) / 2)


def test_slab_savings_api(client, auth, plant, db):
    pid = plant["id"]
    seed_days(db, pid, n=40)
    bad = client.patch(
        f"/api/plants/{pid}",
        headers=auth,
        json={"tariff_slabs": [{"rate": 4}, {"up_to_kwh": 100, "rate": 5}]},
    )
    assert bad.status_code == 422
    r = client.patch(
        f"/api/plants/{pid}",
        headers=auth,
        json={"tariff_slabs": MSEB, "tariff_export_per_kwh": 2.82, "tariff_import_per_kwh": None},
    )
    assert r.status_code == 200 and r.json()["tariff_slabs"][1] == {"up_to_kwh": 300.0, "rate": 11.1}

    s = client.get(f"/api/plants/{pid}/analytics/savings", headers=auth).json()
    assert "estimated_value" not in s and any(
        "monthly household consumption" in m for m in s["missing_inputs"]
    )
    assert s["estimated_value_if_all_self_consumed"] == pytest.approx(s["generation_kwh"] * 17.68, rel=1e-3)

    client.patch(f"/api/plants/{pid}", headers=auth, json={"monthly_consumption_kwh": 400})
    s = client.get(f"/api/plants/{pid}/analytics/savings", headers=auth).json()
    assert s["missing_inputs"] == []
    assert (
        s["estimated_value_if_all_exported"]
        < s["estimated_value"]
        < s["estimated_value_if_all_self_consumed"]
    )
    assert s["bill_without_solar_per_month"] == pytest.approx(4186) and s["marginal_rate_per_kwh"] == 15.38
    assert sum(m["value"] for m in s["months"]) == pytest.approx(s["estimated_value"], abs=0.05)
    monthly = client.get(f"/api/plants/{pid}/analytics/savings/monthly", headers=auth).json()
    assert monthly["items"] and "surplus_kwh" in monthly["items"][0]
