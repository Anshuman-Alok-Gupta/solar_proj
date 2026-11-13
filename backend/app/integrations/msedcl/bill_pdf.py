"""Read an MSEDCL (Mahavitaran) e-bill PDF and keep only the numbers needed for savings.

Privacy
-------
* Runs entirely in this process with ``pypdf``: no AI model or external service sees the bill.
* Only an allow-list of numbers is read (meter units, solar bank units, charges, tariff rates,
  reading/bill dates, connection category and sanctioned load). Name, address, consumer number,
  meter number, billing unit, mobile, bill number, bank details, QR/bar codes, security deposit
  and payment history are never extracted, so they cannot be stored or forwarded.
* The caller passes the PDF as bytes and nothing is written to disk here.

Layout
------
The Marathi labels use a legacy (non-Unicode) font and extract as garbage, so fields are located
from the English labels (``Import``, ``Bank Solar Units``, ``Bill Period``...), from the values'
positions in the tables, and from a few anchors (``@ 1.60 Rs/U`` marks the wheeling-charge row,
``16.00%`` the electricity-duty row). Every section is read independently: anything that cannot
be found is left empty with a warning, so the user can fill it in on the review form.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

NUM = re.compile(r"^-?\d+(?:\.\d+)?$")
DATE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
SLAB = re.compile(r"^(?:(\d+)-(\d+)|>\s*(\d+))$")
ROW_TOL = 2.5
MAX_PAGES = 6


class BillParseError(ValueError):
    """The file is not a readable MSEDCL e-bill PDF."""


@dataclass(frozen=True)
class Item:
    page: int
    x: float
    y: float  # PDF user space: grows upwards
    text: str


@dataclass
class Page:
    width: float
    height: float
    items: list[Item]


# --------------------------------------------------------------------------- extraction


def _pages(pdf: bytes) -> list[Page]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if not pdf.startswith(b"%PDF"):
        raise BillParseError("This file is not a PDF.")
    try:
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted:
            raise BillParseError("This PDF is password-protected.")
        if len(reader.pages) > MAX_PAGES:
            raise BillParseError("This PDF has too many pages to be an MSEDCL bill.")
        pages: list[Page] = []
        for pno, page in enumerate(reader.pages):
            items: list[Item] = []

            def visit(text, cm, tm, _font, _size, pno=pno, items=items):
                t = (text or "").strip()
                if not t:
                    return
                x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
                y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
                items.append(Item(pno, float(x), float(y), t))

            page.extract_text(visitor_text=visit)
            box = page.mediabox
            pages.append(Page(float(box.width), float(box.height), items))
        return pages
    except BillParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise BillParseError("Could not read this PDF.") from exc


# --------------------------------------------------------------------------- helpers


def _num(t: str) -> float | None:
    t = t.replace(",", "").strip()
    return float(t) if NUM.match(t) else None


def _date(t: str) -> date | None:
    m = DATE.match(t.strip())
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _find(items: list[Item], pattern: str, flags: int = 0) -> Item | None:
    rx = re.compile(pattern, flags)
    return next((i for i in items if rx.search(i.text)), None)


def _numbers(items: list[Item], *, y_min: float, y_max: float, x_min: float = -1e9, x_max: float = 1e9):
    """Numeric items inside a box, as (item, value)."""
    out = []
    for i in items:
        if y_min <= i.y <= y_max and x_min <= i.x <= x_max:
            v = _num(i.text)
            if v is not None:
                out.append((i, v))
    return out


def _row_numbers(items: list[Item], anchor: Item, *, after_x: bool = True) -> list[float]:
    vals = _numbers(items, y_min=anchor.y - ROW_TOL, y_max=anchor.y + ROW_TOL)
    vals = [(i, v) for i, v in vals if not after_x or i.x > anchor.x]
    return [v for _, v in sorted(vals, key=lambda p: p[0].x)]


# --------------------------------------------------------------------------- sections


def _header(p0: Page, out: dict[str, Any], warn: list[str]) -> None:
    items = p0.items
    m = _find(items, r"Bill Period:\s*([\d.]+)")
    if m:
        out["bill_period_months"] = float(re.search(r"Bill Period:\s*([\d.]+)", m.text).group(1))  # type: ignore[union-attr]
    else:
        warn.append("Bill period not found.")

    out["net_metered"] = _find(items, r"SOLAR NET METER") is not None
    cat = _find(items, r"\bLT\s*[IVX]+\b")
    if cat:
        out["tariff_category"] = re.sub(r"^\s*\d+\s*/\s*", "", cat.text)[:64]
    load = _find(items, r"^\d+(?:\.\d+)?\s*KW$", re.I)
    if load:
        out["sanctioned_load_kw"] = float(re.match(r"[\d.]+", load.text).group(0))  # type: ignore[union-attr]

    # Bill date and due date sit in the top-right box, newest first: bill date, then due date.
    top_right = sorted(
        (i for i in items if i.x > 0.75 * p0.width and i.y > 0.8 * p0.height and _date(i.text)),
        key=lambda i: -i.y,
    )
    if top_right:
        out["bill_date"] = _date(top_right[0].text)
        if len(top_right) > 1:
            out["due_date"] = _date(top_right[1].text)
    else:
        warn.append("Bill date not found.")
    amounts = sorted(
        _numbers(items, y_min=0.8 * p0.height, y_max=p0.height, x_min=0.85 * p0.width),
        key=lambda p: -p[0].y,
    )
    if amounts:
        out["amount_payable"] = amounts[0][1]

    # Meter-reading dates: the date column under the sanctioned load, below it.
    if load:
        col = sorted(
            (i for i in items if abs(i.x - load.x) < 25 and load.y - 60 < i.y < load.y - 2 and _date(i.text)),
            key=lambda i: -i.y,
        )
        ds = sorted(d for d in (_date(i.text) for i in col[:2]) if d)
        if len(ds) == 2:
            out["reading_from"], out["reading_to"] = ds
    if "reading_to" not in out:
        warn.append("Meter-reading dates not found.")


def _meters(p0: Page, out: dict[str, Any], warn: list[str]) -> None:
    items = p0.items
    for label, key in (("Import", "import_kwh"), ("Export", "export_kwh"), ("Generation", "generation_kwh")):
        anchor = next((i for i in items if i.text == label), None)
        vals = _row_numbers(items, anchor) if anchor else []
        if len(vals) >= 4:
            cur, prev, mf, units = vals[0], vals[1], vals[2], vals[-1]
            out[key] = units
            if abs((cur - prev) * mf - units) > 0.5 and cur >= prev:
                warn.append(
                    f"{label}: readings {cur:g} - {prev:g} don't match {units:g} units; please check."
                )
        elif key != "generation_kwh":
            warn.append(f"{label} units not found.")

    bank = _find(items, r"^Bank Solar Units$")
    if bank:
        row = sorted(
            _numbers(items, y_min=bank.y - 30, y_max=bank.y - 5, x_max=0.7 * p0.width), key=lambda p: p[0].x
        )
        vals = [v for _, v in row]
        if len(vals) == 3:
            out["export_offset_kwh"], out["banked_kwh"], out["prev_banked_kwh"] = vals
    if "banked_kwh" not in out and out.get("net_metered"):
        warn.append("Solar bank units not found.")

    # Billing summary row: current/previous reading, multiplier, units, adjusted units, total billed.
    status = _find(items, r"^Meter Status")
    if status:
        rows: dict[float, list[tuple[Item, float]]] = {}
        for i, v in _numbers(items, y_min=status.y, y_max=status.y + 60, x_max=0.7 * p0.width):
            key = next((k for k in rows if abs(k - i.y) <= ROW_TOL), i.y)
            rows.setdefault(key, []).append((i, v))
        full = [sorted(r, key=lambda p: p[0].x) for r in rows.values() if len(r) >= 5]
        if full:
            out["billed_kwh"] = full[0][-1][1]
    if "billed_kwh" not in out:
        warn.append("Billed units not found.")


CHARGE_KEYS = (
    "fixed_charge",
    "energy_charge",
    "wheeling_charge",
    "fac_charge",
    "electricity_duty",
    "tax_on_sale",
    "other_charges",
)


def _charges(pages: list[Page], out: dict[str, Any], warn: list[str]) -> None:
    for pg in pages:
        wheel = _find(pg.items, r"^@\s*\d+(?:\.\d+)?$")
        if not wheel:
            continue
        out.setdefault("tariff", {})["wheeling_per_kwh"] = float(wheel.text.lstrip("@ ").strip())
        col = sorted(
            _numbers(pg.items, y_min=wheel.y - 200, y_max=wheel.y + 40, x_min=0.85 * pg.width),
            key=lambda p: -p[0].y,
        )
        idx = next((k for k, (i, _) in enumerate(col) if abs(i.y - wheel.y) <= ROW_TOL), None)
        if idx is None or idx < 2 or len(col) < idx + 6:
            break
        vals = [v for _, v in col]
        charges = dict(zip(CHARGE_KEYS, vals[idx - 2 : idx + 5], strict=True))
        current = vals[idx + 5]
        if abs(sum(charges.values()) - current) <= 0.05:
            out.update(charges)
            out["current_bill"] = current
        else:
            warn.append("Charges table didn't add up; please enter the charges by hand.")
        duty = _find(pg.items, r"^\d+(?:\.\d+)?%$")
        if duty:
            out["tariff"]["duty_pct"] = float(duty.text.rstrip("%"))
        return
    warn.append("Charges table not found.")


def _tariff(pages: list[Page], out: dict[str, Any], warn: list[str]) -> None:
    for pg in pages:
        heads = [i for i in pg.items if SLAB.match(i.text.replace(" ", ""))]
        if len(heads) < 2:
            continue
        hy = heads[0].y
        heads = sorted((h for h in heads if abs(h.y - hy) <= ROW_TOL), key=lambda h: h.x)
        below = _numbers(pg.items, y_min=hy - 45, y_max=hy - 3, x_min=heads[0].x - 20, x_max=heads[-1].x + 30)
        ys: list[float] = []
        for i, _ in sorted(below, key=lambda p: -p[0].y):
            if not any(abs(y - i.y) <= ROW_TOL for y in ys):
                ys.append(i.y)
        if not ys:
            break

        def rate(row_y: float, h: Item, below=below) -> float | None:
            near = [v for i, v in below if abs(i.y - row_y) <= ROW_TOL and abs(i.x - h.x) < 20]
            return near[0] if near else None

        slabs = []
        for h in heads:
            m = SLAB.match(h.text.replace(" ", ""))
            up_to = float(m.group(2)) if m and m.group(2) else None  # type: ignore[union-attr]
            slabs.append(
                {
                    "up_to_kwh": up_to,
                    "energy_rate": rate(ys[0], h),
                    "fac_rate": rate(ys[1], h) if len(ys) > 1 else None,
                }
            )
        if all(s["energy_rate"] is not None for s in slabs):
            out.setdefault("tariff", {})["slabs"] = slabs
            return
        break
    warn.append("Tariff slab rates not found.")


# --------------------------------------------------------------------------- entry point


def parse_bill(pdf: bytes) -> dict[str, Any]:
    """Return ``{"fields": {...}, "warnings": [...]}`` with only the allow-listed numbers."""
    pages = _pages(pdf)
    text = " ".join(i.text for p in pages for i in p.items)
    if "BILL OF SUPPLY" not in text or not re.search(r"MSEDCL|mahadiscom", text, re.I):
        raise BillParseError("This doesn't look like an MSEDCL (Mahavitaran) e-bill.")

    out: dict[str, Any] = {}
    warn: list[str] = []
    for section in (
        lambda: _header(pages[0], out, warn),
        lambda: _meters(pages[0], out, warn),
        lambda: _charges(pages, out, warn),
        lambda: _tariff(pages, out, warn),
    ):
        try:
            section()
        except (ValueError, IndexError, AttributeError, TypeError):
            warn.append("Part of the bill could not be read; please check the values.")

    if out.get("bill_date"):
        out["bill_month"] = out["bill_date"].replace(day=1)
    elif out.get("reading_to"):
        out["bill_month"] = out["reading_to"].replace(day=1)
    if out.get("tariff") is not None and "slabs" not in out["tariff"]:
        out["tariff"]["slabs"] = []
    return {"fields": out, "warnings": warn}


def iso(fields: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly copy (dates as ISO strings)."""
    return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in fields.items()}
