"""Savings measured from electricity bills (MSEDCL net metering).

For each bill's meter-reading period:

* household consumption = import + generation - export (generation from the bill's solar
  generation meter, else the inverter's daily totals for the same days);
* the bill without solar = the bill's own tariff applied to that consumption: energy charge and
  fuel adjustment (FAC) per slab, wheeling per unit, electricity duty on those, plus the same
  fixed and other charges. Slab limits are scaled by the bill period (MSEDCL bills a 25-day
  period as 0.83 of a month);
* savings = bill without solar - what the bill actually charged for the period (arrears and
  adjustments from earlier bills are excluded);
* surplus units go to the solar bank; any still unused at the end of the financial year are paid
  out at the settlement rate (the plant's export rate), so their value is shown separately.

Assumption: electricity duty applies to energy, FAC and wheeling charges but not the fixed
charge. This matches the first bill (fixed charge 445, duty 0). Each bill with billed units is
re-computed against its own charges, so ``check`` shows how well the model matches the bill.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Plant, UtilityBill
from app.services.analytics import daily_frame
from app.services.analytics import savings as estimate_savings

CHARGE_FIELDS = (
    "fixed_charge",
    "energy_charge",
    "wheeling_charge",
    "fac_charge",
    "electricity_duty",
    "tax_on_sale",
    "other_charges",
)


def _r(v: float | None, nd: int = 2) -> float | None:
    return None if v is None else round(float(v), nd)


def period_months(bill: UtilityBill) -> float:
    if bill.bill_period_months:
        return float(bill.bill_period_months)
    return round((bill.reading_to - bill.reading_from).days / 30, 2)


def variable_charges(units: float, tariff: dict[str, Any], months: float) -> dict[str, float]:
    """Energy, FAC, wheeling and duty for ``units`` over a period of ``months``."""
    energy = fac = 0.0
    prev = 0.0
    remaining = max(units, 0.0)
    for s in tariff.get("slabs") or []:
        top = s.get("up_to_kwh")
        cap = float("inf") if top is None else float(top) * months
        take = min(remaining, max(cap - prev, 0.0))
        energy += take * float(s.get("energy_rate") or 0)
        fac += take * float(s.get("fac_rate") or 0)
        remaining -= take
        prev = cap
        if remaining <= 0:
            break
    if remaining > 0 and tariff.get("slabs"):  # beyond the last bounded slab
        last = tariff["slabs"][-1]
        energy += remaining * float(last.get("energy_rate") or 0)
        fac += remaining * float(last.get("fac_rate") or 0)
    wheeling = max(units, 0.0) * float(tariff.get("wheeling_per_kwh") or 0)
    duty = (energy + fac + wheeling) * float(tariff.get("duty_pct") or 0) / 100
    total = energy + fac + wheeling + duty
    return {
        k: round(v, 2)
        for k, v in (
            ("energy", energy),
            ("fac", fac),
            ("wheeling", wheeling),
            ("duty", duty),
            ("total", total),
        )
    }


def _tariff_for(bill: UtilityBill, plant: Plant, notes: list[str]) -> dict[str, Any] | None:
    t = bill.tariff or {}
    if t.get("slabs"):
        return t
    if plant.tariff_slabs:
        notes.append(
            "This bill has no tariff table, so the slab rates from Settings are used "
            "(energy charge only: no fuel adjustment, wheeling or duty)."
        )
        return {
            "slabs": [
                {"up_to_kwh": s.get("up_to_kwh"), "energy_rate": s.get("rate")} for s in plant.tariff_slabs
            ]
        }
    return None


def inverter_generation(db: Session, plant: Plant, bill: UtilityBill) -> dict[str, Any]:
    """Inverter (SEMS) generation for the days after the previous reading up to the current one."""
    start = bill.reading_from + timedelta(days=1)
    df = daily_frame(db, plant, start, bill.reading_to)
    days = (bill.reading_to - bill.reading_from).days
    have = int(df["generation_kwh"].notna().sum()) if not df.empty else 0
    total = float(df["generation_kwh"].sum()) if have else None
    return {"kwh": _r(total, 1), "days_with_data": have, "days": days, "complete": have >= days}


def analyse(db: Session, plant: Plant, bill: UtilityBill) -> dict[str, Any]:
    notes: list[str] = []
    inv = inverter_generation(db, plant, bill)
    if bill.generation_kwh is not None:
        gen, gen_source = float(bill.generation_kwh), "bill"
    elif inv["kwh"] is not None:
        gen, gen_source = float(inv["kwh"]), "inverter"
        if not inv["complete"]:
            notes.append(
                f"Inverter data covers {inv['days_with_data']} of {inv['days']} days, so generation "
                "and the savings are understated."
            )
    else:
        gen, gen_source = None, None

    out: dict[str, Any] = {
        "period_months": period_months(bill),
        "days": (bill.reading_to - bill.reading_from).days,
        "generation_kwh": _r(gen, 1),
        "generation_source": gen_source,
        "inverter_generation": inv,
        "notes": notes,
    }
    if bill.generation_kwh is not None and inv["kwh"] and inv["complete"]:
        out["inverter_vs_meter_pct"] = round(
            (inv["kwh"] - bill.generation_kwh) / bill.generation_kwh * 100, 1
        )

    offset = (
        bill.export_offset_kwh
        if bill.export_offset_kwh is not None
        else min(bill.import_kwh, bill.export_kwh)
    )
    banked_added = max(bill.export_kwh - offset, 0.0)
    rate = plant.tariff_export_per_kwh
    out["bank"] = {
        "offset_kwh": _r(offset, 1),
        "added_kwh": _r(banked_added, 1),
        "balance_kwh": _r(bill.banked_kwh, 1),
        "settlement_rate": rate,
        "added_value_at_settlement": _r(banked_added * rate) if rate else None,
    }

    actual = bill.current_bill
    if actual is None:
        parts = [getattr(bill, k) for k in CHARGE_FIELDS]
        actual = sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None
    out["actual_bill"] = _r(actual)

    if gen is None:
        notes.append("No generation for this period (bill or inverter), so consumption can't be worked out.")
        return out
    consumption = bill.import_kwh + gen - bill.export_kwh
    self_used = max(gen - bill.export_kwh, 0.0)
    out.update(
        consumption_kwh=_r(consumption, 1),
        self_consumed_kwh=_r(self_used, 1),
        self_consumption_pct=_r(self_used / gen * 100, 1) if gen > 0 else None,
        solar_share_pct=_r((self_used + offset) / consumption * 100, 1) if consumption > 0 else None,
    )
    if consumption < 0:
        notes.append("Export is larger than import plus generation; please check the units.")
        return out

    tariff = _tariff_for(bill, plant, notes)
    if tariff is None:
        notes.append("No tariff on the bill or in Settings, so the bill without solar can't be estimated.")
        return out
    months = out["period_months"]
    var = variable_charges(consumption, tariff, months)
    fixed = float(bill.fixed_charge or 0) + float(bill.other_charges or 0)
    without = var["total"] + fixed
    out["without_solar"] = {**var, "fixed_and_other": _r(fixed), "total": _r(without)}
    if actual is not None:
        out["savings"] = _r(without - actual)
        out["savings_per_generated_kwh"] = _r((without - actual) / gen) if gen > 0 else None

    if bill.billed_kwh and bill.billed_kwh > 0:
        on_bill = sum(
            float(getattr(bill, k) or 0)
            for k in ("energy_charge", "fac_charge", "wheeling_charge", "electricity_duty")
        )
        out["check"] = {
            "billed_kwh": bill.billed_kwh,
            "computed_variable_charges": variable_charges(bill.billed_kwh, tariff, months)["total"],
            "variable_charges_on_bill": _r(on_bill),
        }
    return out


def bills_overview(db: Session, plant: Plant) -> dict[str, Any]:
    bills = db.scalars(
        select(UtilityBill).where(UtilityBill.plant_id == plant.id).order_by(UtilityBill.reading_to)
    ).all()
    rows = [(b, analyse(db, plant, b)) for b in bills]
    with_savings = [a for _, a in rows if a.get("savings") is not None]
    months = sum(a["period_months"] for a in with_savings)
    total = sum(a["savings"] for a in with_savings)
    latest = bills[-1] if bills else None
    rate = plant.tariff_export_per_kwh
    summary = {
        "bills": len(bills),
        "bills_with_savings": len(with_savings),
        "total_savings": _r(total) if with_savings else None,
        "total_without_solar": _r(sum(a["without_solar"]["total"] for a in with_savings))
        if with_savings
        else None,
        "total_actual": _r(sum(a["actual_bill"] or 0 for a in with_savings)) if with_savings else None,
        "avg_monthly_savings": _r(total / months) if months else None,
        "consumption_kwh": _r(sum(a.get("consumption_kwh") or 0 for _, a in rows), 1) if rows else None,
        "generation_kwh": _r(sum(a.get("generation_kwh") or 0 for _, a in rows), 1) if rows else None,
        "bank_balance_kwh": latest.banked_kwh if latest else None,
        "bank_value_at_settlement": _r(latest.banked_kwh * rate)
        if latest and latest.banked_kwh and rate
        else None,
        "first_reading": bills[0].reading_from.isoformat() if bills else None,
        "last_reading": latest.reading_to.isoformat() if latest else None,
        "to_date": savings_to_date(db, plant, [(b, a) for b, a in rows if a.get("savings") is not None]),
    }
    return {"bills": rows, "summary": summary}


def savings_to_date(
    db: Session, plant: Plant, measured: list[tuple[UtilityBill, dict[str, Any]]]
) -> dict[str, Any] | None:
    """Savings to date: measured from bills where a bill covers the days, estimated elsewhere.

    A bill covers reading_from+1 .. reading_to (the same days as inverter_generation). Days with
    inverter data outside every bill with savings (before the first reading, between bills, and
    since the last reading, i.e. not billed yet) are valued with the tariff estimate.
    """
    if not measured:
        return None
    total_measured = sum(a["savings"] for _, a in measured)
    periods = sorted((b.reading_from + timedelta(days=1), b.reading_to) for b, _ in measured)

    gaps: list[tuple[date | None, date | None]] = []
    cursor: date | None = None  # first day not yet covered
    for start, end in periods:
        if cursor is None or start > cursor:
            gaps.append((cursor, start - timedelta(days=1)))
        cursor = max(cursor, end + timedelta(days=1)) if cursor else end + timedelta(days=1)
    gaps.append((cursor, None))

    unbilled: list[dict[str, Any]] = []
    estimate_missing = False
    for start, end in gaps:
        est = estimate_savings(db, plant, start, end)
        days = est["period"]["days"]
        if not days:
            continue
        value = est.get("estimated_value")
        if value is None:
            estimate_missing = True
        unbilled.append(
            {
                "start": est["period"]["start"],
                "end": est["period"]["end"],
                "days": days,
                "generation_kwh": est["generation_kwh"],
                "estimated_savings": value,
            }
        )
    estimated = sum(u["estimated_savings"] or 0 for u in unbilled)
    return {
        "total": _r(total_measured + estimated),
        "measured": _r(total_measured),
        "measured_bills": len(measured),
        "measured_from": periods[0][0].isoformat(),
        "measured_to": periods[-1][1].isoformat(),
        "estimated": _r(estimated) if unbilled else None,
        "estimated_days": sum(u["days"] for u in unbilled),
        "estimate_missing_tariff": estimate_missing,
        "unbilled": unbilled,
    }
