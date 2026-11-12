"""Slab (tiered) tariffs under net metering, e.g. MSEB/MSEDCL residential.

Model
-----
* The household uses ``C`` units in a month (``Plant.monthly_consumption_kwh``, the typical
  pre-solar figure from a bill). Without a smart meter this is the only consumption input.
* Under net metering every generated unit cancels a unit that would have been billed, so the
  generated units come off the **top** of the slab ladder: the saving is
  ``cost(C) - cost(C - offset)`` with ``offset = min(G, C)``.
* Generation beyond ``C`` is surplus. MSEB banks it and pays the year-end settlement rate
  (``tariff_export_per_kwh``) for whatever is left; valuing each month's surplus at that rate is
  the conservative case (banked units used in a later low-generation month would be worth more).
* Partial months (data started mid-month, current month) are scaled to a full month for the
  slab arithmetic and scaled back, so slab thresholds stay monthly.
"""

from __future__ import annotations

import calendar
from typing import Any

import pandas as pd


def slab_cost(units: float, slabs: list[dict[str, Any]]) -> float:
    """Energy charge for ``units`` in one month. ``slabs`` are ordered; the last may be open-ended."""
    if units <= 0 or not slabs:
        return 0.0
    cost, prev = 0.0, 0.0
    for s in slabs:
        top = s.get("up_to_kwh")
        rate = float(s["rate"])
        if top is None or units <= top:
            return cost + (units - prev) * rate
        cost += (float(top) - prev) * rate
        prev = float(top)
    # beyond the last bounded slab: charge its rate
    return cost + (units - prev) * float(slabs[-1]["rate"])


def marginal_rate(units: float, slabs: list[dict[str, Any]]) -> float | None:
    """Rate of the slab the ``units``-th unit falls in."""
    for s in slabs:
        top = s.get("up_to_kwh")
        if top is None or units <= top:
            return float(s["rate"])
    return float(slabs[-1]["rate"]) if slabs else None


def month_value(
    gen_kwh: float, consumption_kwh: float, slabs: list[dict[str, Any]], export_rate: float, coverage: float
) -> dict[str, float]:
    """Value of one (possibly partial) month of generation. ``coverage`` = fraction of the month observed."""
    coverage = min(max(coverage, 1e-9), 1.0)
    g_full = gen_kwh / coverage
    offset = min(g_full, consumption_kwh)
    bill_saving = slab_cost(consumption_kwh, slabs) - slab_cost(consumption_kwh - offset, slabs)
    surplus = max(g_full - consumption_kwh, 0.0)
    return {
        "bill_saving": bill_saving * coverage,
        "surplus_kwh": surplus * coverage,
        "surplus_value": surplus * export_rate * coverage,
        "offset_kwh": offset * coverage,
        "value": (bill_saving + surplus * export_rate) * coverage,
    }


def monthly_breakdown(
    df: pd.DataFrame, consumption_kwh: float, slabs: list[dict[str, Any]], export_rate: float
) -> list[dict[str, Any]]:
    """Per calendar month: generation, offset vs surplus units and estimated value."""
    if df.empty:
        return []
    d = df[["date", "generation_kwh"]].copy()
    d["date"] = pd.to_datetime(d["date"])
    d["month"] = d["date"].dt.to_period("M")
    out = []
    for month, g in d.groupby("month"):
        days_in_month = calendar.monthrange(month.year, month.month)[1]
        coverage = g["date"].nunique() / days_in_month
        gen = float(g["generation_kwh"].sum())
        v = month_value(gen, consumption_kwh, slabs, export_rate, coverage)
        out.append(
            {
                "month": str(month),
                "days_with_data": int(g["date"].nunique()),
                "generation_kwh": round(gen, 2),
                "offset_kwh": round(v["offset_kwh"], 2),
                "surplus_kwh": round(v["surplus_kwh"], 2),
                "bill_saving": round(v["bill_saving"], 2),
                "surplus_value": round(v["surplus_value"], 2),
                "value": round(v["value"], 2),
            }
        )
    return out
