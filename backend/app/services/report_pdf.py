"""Render a stored performance report as a PDF (fpdf2, pure Python, no system deps).

Only data already in ``Report.content`` is used, so the PDF matches the on-screen report
and the CSV export exactly. Core PDF fonts are Latin-1, so text is normalised first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.db.models import Plant, Report

_REPLACE = {
    "–": "-",
    "—": "-",
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    "≤": "<=",
    "≥": ">=",
    "₹": "INR ",
    " ": " ",
}

AMBER = (217, 119, 6)
GREY = (110, 110, 110)
LIGHT = (235, 235, 235)


def _t(value: Any) -> str:
    s = "-" if value is None else str(value)
    for k, v in _REPLACE.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _n(value: Any, digits: int = 1, unit: str = "") -> str:
    if value is None:
        return "-"
    try:
        out = f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return _t(value)
    return f"{out} {unit}".strip()


class _PDF(FPDF):
    footer_text = ""

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", size=7)
        self.set_text_color(*GREY)
        self.cell(0, 5, _t(self.footer_text), align="L")
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}}", align="R")


def _heading(pdf: FPDF, text: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*AMBER)
    pdf.cell(0, 7, _t(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", size=9)


def _kv_table(pdf: FPDF, rows: list[tuple[str, str]]) -> None:
    pdf.set_font("Helvetica", size=9)
    for label, value in rows:
        pdf.set_text_color(*GREY)
        pdf.cell(60, 5.5, _t(label))
        pdf.set_text_color(0, 0, 0)
        pdf.cell(0, 5.5, _t(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _table(pdf: FPDF, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(*LIGHT)
    for h, w in zip(headers, widths, strict=True):
        pdf.cell(w, 6, _t(h), border=0, fill=True)
    pdf.ln(6)
    pdf.set_font("Helvetica", size=8)
    for row in rows:
        if pdf.get_y() > pdf.h - 22:
            pdf.add_page()
        for v, w in zip(row, widths, strict=True):
            pdf.cell(w, 5, _t(v))
        pdf.ln(5)


def _bar_chart(pdf: FPDF, daily: list[dict[str, Any]], height: float = 42) -> None:
    vals = [d.get("generation_kwh") or 0.0 for d in daily]
    if not vals:
        return
    x0, y0 = pdf.l_margin, pdf.get_y() + 2
    width = pdf.w - pdf.l_margin - pdf.r_margin
    vmax = max(vals) or 1.0
    gap = 0.6 if len(vals) <= 40 else 0.2
    bw = max(0.4, width / len(vals) - gap)
    pdf.set_draw_color(*LIGHT)
    pdf.line(x0, y0 + height, x0 + width, y0 + height)
    for i, (d, v) in enumerate(zip(daily, vals, strict=True)):
        h = height * v / vmax
        if d.get("is_complete") is False:
            pdf.set_fill_color(252, 211, 77)  # partial day: lighter amber
        else:
            pdf.set_fill_color(*AMBER)
        pdf.rect(x0 + i * (bw + gap), y0 + height - h, bw, h, style="F")
    pdf.set_font("Helvetica", size=7)
    pdf.set_text_color(*GREY)
    pdf.set_xy(x0, y0 + height + 1)
    pdf.cell(width / 2, 4, _t(daily[0]["date"]))
    pdf.cell(width / 2, 4, _t(daily[-1]["date"]), align="R")
    pdf.set_xy(x0, y0 - 1)
    pdf.cell(width, 4, f"max {_n(vmax, 1, 'kWh')}", align="R")
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(y0 + height + 6)


def report_pdf(rep: Report, plant: Plant) -> bytes:
    c: dict[str, Any] = rep.content or {}
    period = c.get("period", {})
    gen = c.get("generation", {})
    wx = c.get("weather", {})
    sav = c.get("savings", {})
    cur = sav.get("currency") or plant.currency

    pdf = _PDF(format="A4")
    pdf.footer_text = (
        f"{plant.name} - generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} from stored data. "
        "Generation is measured by the inverter; forecasts and savings are estimates."
    )
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 15)
    pdf.multi_cell(0, 7, _t(rep.title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", size=9)
    pdf.set_text_color(*GREY)
    cap = f", {_n(plant.capacity_kw, 1, 'kW')}" if plant.capacity_kw else ""
    pdf.cell(
        0,
        5,
        _t(
            f"{plant.name}{cap} - {period.get('start')} to {period.get('effective_end') or period.get('end')}"
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )
    pdf.set_text_color(0, 0, 0)

    _heading(pdf, "Summary")
    pdf.multi_cell(0, 5, _t(rep.summary_text or ""), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    _heading(pdf, "Generation (measured)")
    best, low = gen.get("best_day"), gen.get("lowest_day")
    _kv_table(
        pdf,
        [
            ("Total", _n(gen.get("total_kwh"), 2, "kWh")),
            ("Average per complete day", _n(gen.get("avg_daily_kwh"), 2, "kWh")),
            ("Specific yield", _n(gen.get("specific_yield_kwh_per_kw"), 2, "kWh/kW")),
            (
                "Days with data / complete",
                f"{period.get('days_with_data', 0)} / {period.get('complete_days', 0)}",
            ),
            ("Best day", f"{best['date']} ({_n(best['kwh'], 2, 'kWh')})" if best else "-"),
            ("Lowest day", f"{low['date']} ({_n(low['kwh'], 2, 'kWh')})" if low else "-"),
            (
                "Previous period",
                f"{_n(gen.get('previous_period_total_kwh'), 2, 'kWh')}"
                + (
                    f" ({gen['change_vs_previous_pct']:+.1f}%)"
                    if gen.get("change_vs_previous_pct") is not None
                    else ""
                ),
            ),
        ],
    )
    daily = c.get("daily", [])
    if len(daily) > 1:
        _bar_chart(pdf, daily)

    _heading(pdf, "Weather (observed / archived forecast)")
    _kv_table(
        pdf,
        [
            ("Mean tilted irradiance", _n(wx.get("mean_gti_wh_m2"), 0, "Wh/m²")),
            ("Mean cloud cover", _n(wx.get("mean_cloud_cover_pct"), 0, "%")),
            ("Mean max temperature", _n(wx.get("mean_temp_max_c"), 1, "°C")),
            (
                "Sunniest / cloudiest day",
                f"{wx.get('sunniest_day') or '-'} / {wx.get('cloudiest_day') or '-'}",
            ),
        ],
    )

    _heading(pdf, "Anomalies")
    anomalies = c.get("anomalies", [])
    if anomalies:
        _table(
            pdf,
            ["Date", "Severity", "Status", "Actual", "Expected", "Title"],
            [
                [
                    a.get("date"),
                    a.get("severity"),
                    a.get("status"),
                    _n(a.get("actual_kwh"), 1),
                    _n(a.get("expected_kwh"), 1),
                    (a.get("title") or "")[:60],
                ]
                for a in anomalies
            ],
            [22, 18, 24, 16, 18, 92],
        )
    else:
        pdf.cell(0, 5, "No anomalies recorded in this period.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    _heading(pdf, "Forecasting (estimates)")
    fp, nf, model = c.get("forecast_performance", {}), c.get("next_forecast"), c.get("model")
    _kv_table(
        pdf,
        [
            ("Active model", f"{model['algorithm']} (v{model['model_version_id']})" if model else "-"),
            ("Model test MAE", _n(model.get("test_mae"), 2, "kWh") if model else "-"),
            ("Live forecasts scored", str(fp.get("scored_days", 0))),
            ("Live forecast MAE", _n(fp.get("mae_kwh"), 2, "kWh")),
            (
                "Next forecast",
                f"{nf['target_date']}: {_n(nf['predicted_kwh'], 1, 'kWh')} "
                f"({_n(nf.get('lower_kwh'), 1)}-{_n(nf.get('upper_kwh'), 1)})"
                if nf
                else "-",
            ),
        ],
    )

    _heading(pdf, "Value (estimate)")
    ev = sav.get("estimated_value")
    rows = [("Estimated value", f"{cur} {_n(ev, 0)}" if ev is not None else "not estimated")]
    if sav.get("estimated_value_if_all_exported") is not None:
        rows.append(
            (
                "Range (all exported - all self-used)",
                f"{cur} {_n(sav.get('estimated_value_if_all_exported'), 0)} - "
                f"{_n(sav.get('estimated_value_if_all_self_consumed'), 0)}",
            )
        )
    _kv_table(pdf, rows)
    for note in [*(sav.get("missing_inputs") or []), *(sav.get("notes") or [])]:
        pdf.set_text_color(*GREY)
        pdf.multi_cell(0, 4.5, _t(f"- {note}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)

    if daily:
        pdf.add_page()
        _heading(pdf, "Daily detail")
        _table(
            pdf,
            ["Date", "Generation (kWh)", "Irradiance (Wh/m2)", "Cloud cover (%)", "Complete"],
            [
                [
                    d.get("date"),
                    _n(d.get("generation_kwh"), 2),
                    _n(d.get("gti_wh"), 0),
                    _n(d.get("cloud_cover"), 0),
                    "yes" if d.get("is_complete") else "partial",
                ]
                for d in daily
            ],
            [30, 36, 40, 36, 30],
        )
        sources = ", ".join(c.get("data_sources") or [])
        if sources:
            pdf.ln(2)
            pdf.set_text_color(*GREY)
            pdf.cell(0, 5, _t(f"Data sources: {sources}"))
            pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())
