"""MSEDCL bill reader (local, no AI) and savings measured from bills."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from app.integrations.msedcl.bill_pdf import BillParseError, iso, parse_bill
from app.services.bills import variable_charges
from tests.conftest import seed_days

# Fake personal details: the reader must never return any of these.
FAKE_NAME = "SHRI TEST PERSON SAMPLE"
FAKE_ADDRESS = "FLAT 1 SAMPLE SOCIETY TESTNAGAR 411000"
FAKE_CONSUMER = "999988887777"
FAKE_METER = "100M00000000001"
PRIVATE = [FAKE_NAME, FAKE_ADDRESS, FAKE_CONSUMER, FAKE_METER, "99xxxxxx11", "000001234567890"]

H = 822.0
# (page, x, y-from-bottom, text): the layout of a real MSEDCL net-metering e-bill (Oct 2026)
LAYOUT = [
    # page 1: header, private details, top-right box
    (0, 183, 779.7, "BILL OF SUPPLY FOR THE MONTH OF"),
    (0, 17.8, 764.1, "BILL NO.(GGN):"),
    (0, 73, 763.7, "000001234567890"),
    (0, 79, 747.6, FAKE_CONSUMER),
    (0, 219, 746.6, "99xxxxxx11"),
    (0, 278, 747.6, "SOLAR NET METER"),
    (0, 356, 747.6, "(6.00KW)"),
    (0, 523.1, 741.6, "21-10-2026"),
    (0, 14, 732.0, FAKE_NAME),
    (0, 539.5, 723.7, "440.00"),
    (0, 14, 721.6, FAKE_ADDRESS),
    (0, 523.1, 703.6, "12-11-2026"),
    (0, 539.5, 688.6, "440.00"),
    (0, 246, 662.2, "SOLAR AGREEMENT DATE :"),
    (0, 360, 661.6, "24-08-2026"),
    (0, 360, 648.2, "22-02-2008"),
    (0, 76, 636.6, "92/LT I Res 3-Phase"),
    (0, 360, 635.7, "6.00 KW"),
    (0, 360, 623.2, "3794.61"),
    (0, 360, 612.6, "14-10-2026"),
    (0, 76, 599.2, FAKE_METER),
    (0, 360, 597.2, "19-09-2026"),
    # billing summary row: current, previous, multiplier, units, adjusted, total billed
    (0, 31.3, 536.2, "364"),
    (0, 96.3, 536.2, "194"),
    (0, 162.7, 536.2, "1.00"),
    (0, 233.3, 536.2, "0"),
    (0, 301.8, 536.2, "0"),
    (0, 368.3, 536.2, "0"),
    (0, 5, 506.9, "Meter Status: Normal"),
    (0, 5, 499.6, "Bill Period: 0.83/"),
    # history columns (must not leak into the meter table)
    (0, 312.7, 461.6, "194"),
    (0, 353.7, 461.6, "264"),
    (0, 391.7, 461.6, "332"),
    (0, 418.4, 452.7, "www.mahadiscom.in > ConsumerPortal"),
    # solar net meter table
    (0, 175, 465.2, "Consumption"),
    (0, 11, 448.7, "Import"),
    (0, 74.3, 448.7, "364"),
    (0, 123.3, 448.7, "194"),
    (0, 155.9, 448.7, "1.0"),
    (0, 196.3, 448.7, "170"),
    (0, 11, 434.7, "Export"),
    (0, 73.8, 434.7, "566"),
    (0, 123.8, 434.7, "264"),
    (0, 155.9, 434.7, "1.0"),
    (0, 195.8, 434.7, "302"),
    (0, 157.7, 420.1, "1.00"),
    (0, 11, 419.7, "Generation"),
    (0, 74.3, 419.7, "788"),
    (0, 123.3, 419.7, "332"),
    (0, 196.3, 419.7, "456"),
    (0, 9, 407.6, "Export offset"),
    (0, 79, 405.6, "Bank Solar Units"),
    (0, 158, 405.6, "Prev Bank Units"),
    (0, 30.3, 387.7, "170"),
    (0, 104.3, 387.7, "202"),
    (0, 190.6, 387.7, "70"),
    (0, 16, 293.3, f"o Beneficiary Name: MSEDCL    o Beneficiary account no.: MSEDCL01{FAKE_CONSUMER}"),
    # page 2: charges column
    (1, 394, 813.6, FAKE_CONSUMER),
    (1, 522.5, 775.2, "445.00"),
    (1, 531.4, 763.2, "0.00"),
    (1, 391, 751.7, "@ 1.60"),
    (1, 429.2, 751.7, "Rs/U"),
    (1, 531.4, 752.2, "0.00"),
    (1, 531.4, 739.2, "0.00"),
    (1, 393, 728.6, "16.00%"),
    (1, 531.4, 727.2, "0.00"),
    (1, 398, 716.6, "0.00"),
    (1, 531.4, 716.2, "0.00"),
    (1, 529.5, 690.9, "0.00"),
    (1, 519.5, 677.9, "445.00"),
    (1, 531.4, 665.7, "0.00"),
    (1, 531.4, 653.2, "3.47"),
    (1, 528.8, 641.2, "-9.20"),
    (1, 531.4, 628.7, "0.00"),
    (1, 526.5, 615.4, "-5.73"),
    (1, 519.5, 602.4, "439.27"),
    (1, 519.5, 589.4, "440.00"),
    (1, 348, 568.6, "30-10-2026"),
    (1, 520.5, 568.9, "430.00"),
    (1, 450, 557.6, "4.45"),
    # tariff table
    (1, 155.1, 496.6, "0-100"),
    (1, 188.7, 496.6, "101-300"),
    (1, 225.2, 496.6, "301-500"),
    (1, 260.2, 496.6, "501-1000"),
    (1, 302.2, 496.6, ">1000"),
    (1, 157.2, 482.6, "3.96"),
    (1, 192.7, 482.6, "10.80"),
    (1, 229.2, 482.6, "15.03"),
    (1, 266.2, 482.6, "17.53"),
    (1, 303.2, 482.6, "17.53"),
    (1, 155.2, 467.6, "0.250"),
    (1, 192.7, 467.6, "0.500"),
    (1, 229.2, 467.6, "0.650"),
    (1, 266.2, 467.6, "0.700"),
    (1, 303.2, 467.6, "0.700"),
    # payment history (ignored)
    (1, 458.3, 465.9, "PAYMENT HISTORY"),
    (1, 439.7, 444.9, "21-09-2026"),
    (1, 525.6, 444.9, "850.00"),
]


def fake_bill_pdf(layout=LAYOUT) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(unit="pt", format=(575, H))
    pdf.set_font("Helvetica", size=7)
    for p in range(max(i[0] for i in layout) + 1):
        pdf.add_page()
        for page, x, y, text in layout:
            if page == p:
                pdf.text(x, H - y, text)
    return bytes(pdf.output())


def _assert_private_absent(payload: object) -> None:
    blob = json.dumps(payload, default=str)
    for secret in PRIVATE:
        assert secret not in blob
    assert not re.search(r"\d{10,}", blob)  # no consumer/meter/bill numbers of any kind


def test_reads_the_numbers_and_nothing_private():
    res = parse_bill(fake_bill_pdf())
    f = res["fields"]
    assert res["warnings"] == []
    assert f["bill_date"] == date(2026, 10, 21) and f["due_date"] == date(2026, 11, 12)
    assert f["bill_month"] == date(2026, 10, 1)
    assert (f["reading_from"], f["reading_to"]) == (date(2026, 9, 19), date(2026, 10, 14))
    assert f["bill_period_months"] == 0.83 and f["net_metered"] is True
    assert f["tariff_category"] == "LT I Res 3-Phase" and f["sanctioned_load_kw"] == 6.0
    assert (f["import_kwh"], f["export_kwh"], f["generation_kwh"]) == (170, 302, 456)
    assert (f["export_offset_kwh"], f["banked_kwh"], f["prev_banked_kwh"]) == (170, 202, 70)
    assert f["billed_kwh"] == 0
    assert f["fixed_charge"] == 445 and f["energy_charge"] == 0 and f["current_bill"] == 445
    assert f["amount_payable"] == 440
    t = f["tariff"]
    assert t["wheeling_per_kwh"] == 1.6 and t["duty_pct"] == 16
    assert [s["up_to_kwh"] for s in t["slabs"]] == [100, 300, 500, 1000, None]
    assert [s["energy_rate"] for s in t["slabs"]] == [3.96, 10.8, 15.03, 17.53, 17.53]
    assert [s["fac_rate"] for s in t["slabs"]] == [0.25, 0.5, 0.65, 0.7, 0.7]
    _assert_private_absent(iso(f))


def test_missing_sections_become_warnings_not_errors():
    layout = [i for i in LAYOUT if i[3] not in ("Bank Solar Units", "@ 1.60")]
    res = parse_bill(fake_bill_pdf(layout))
    assert "banked_kwh" not in res["fields"] and "fixed_charge" not in res["fields"]
    assert any("bank" in w.lower() for w in res["warnings"])
    assert any("charges" in w.lower() for w in res["warnings"])
    assert res["fields"]["import_kwh"] == 170


def test_rejects_non_bills():
    with pytest.raises(BillParseError):
        parse_bill(b"not a pdf")
    with pytest.raises(BillParseError):
        parse_bill(fake_bill_pdf([(0, 50, 700, "Some other document")]))


PRIVATE_DIR = Path(__file__).resolve().parents[2] / "data" / "samples" / "private" / "bills"


@pytest.mark.skipif(not list(PRIVATE_DIR.glob("*.pdf")), reason="no private sample bills on this machine")
def test_real_bills_on_this_machine():
    """Runs only where real bills exist (git-ignored); checks structure, never prints values."""
    for path in PRIVATE_DIR.glob("*.pdf"):
        res = parse_bill(path.read_bytes())
        f = res["fields"]
        assert res["warnings"] == [], path.name
        for k in ("reading_from", "reading_to", "import_kwh", "export_kwh", "current_bill", "tariff"):
            assert f.get(k) is not None, (path.name, k)
        _assert_private_absent(iso(f))
        assert not re.search(r"SHRI|SMT\.?\s", json.dumps(iso(f), default=str))


TARIFF = {
    "slabs": [
        {"up_to_kwh": 100, "energy_rate": 3.96, "fac_rate": 0.25},
        {"up_to_kwh": 300, "energy_rate": 10.80, "fac_rate": 0.5},
        {"up_to_kwh": 500, "energy_rate": 15.03, "fac_rate": 0.65},
        {"up_to_kwh": None, "energy_rate": 17.53, "fac_rate": 0.7},
    ],
    "wheeling_per_kwh": 1.6,
    "duty_pct": 16,
}


def test_variable_charges_scale_slabs_by_bill_period():
    full = variable_charges(324, TARIFF, 1.0)
    assert full["energy"] == pytest.approx(100 * 3.96 + 200 * 10.8 + 24 * 15.03)
    assert full["wheeling"] == pytest.approx(324 * 1.6)
    part = variable_charges(324, TARIFF, 0.83)  # slabs 83 / 249 / 415 units
    assert part["energy"] == pytest.approx(83 * 3.96 + 166 * 10.8 + 75 * 15.03)
    assert part["fac"] == pytest.approx(83 * 0.25 + 166 * 0.5 + 75 * 0.65)
    assert part["duty"] == pytest.approx((part["energy"] + part["fac"] + part["wheeling"]) * 0.16, abs=0.02)
    assert variable_charges(0, TARIFF, 1.0)["total"] == 0


def test_bills_api_upload_save_and_savings(client, auth, plant, db):
    pid = plant["id"]
    client.patch(f"/api/plants/{pid}", headers=auth, json={"tariff_export_per_kwh": 2.82})
    seed_days(db, pid, n=40, start=date(2026, 9, 10))  # inverter data covering the bill period

    files = {"file": (f"EB2610{FAKE_CONSUMER}.pdf", fake_bill_pdf(), "application/pdf")}
    r = client.post(f"/api/plants/{pid}/bills/parse", headers=auth, files=files)
    assert r.status_code == 200, r.text
    parsed = r.json()
    assert parsed["warnings"] == [] and parsed["existing_bill_id"] is None
    assert parsed["fields"]["source"] == "pdf"
    _assert_private_absent(parsed)

    r = client.post(f"/api/plants/{pid}/bills", headers=auth, json=parsed["fields"])
    assert r.status_code == 201, r.text
    bill = r.json()
    a = bill["analysis"]
    assert a["generation_source"] == "bill" and a["generation_kwh"] == 456
    assert a["consumption_kwh"] == pytest.approx(170 + 456 - 302)
    assert a["self_consumed_kwh"] == pytest.approx(456 - 302)
    expected_var = variable_charges(324, parsed["fields"]["tariff"], 0.83)["total"]
    assert a["without_solar"]["total"] == pytest.approx(expected_var + 445, abs=0.02)
    assert a["actual_bill"] == 445 and a["savings"] == pytest.approx(expected_var, abs=0.02)
    assert a["bank"] == {
        "offset_kwh": 170,
        "added_kwh": 132,
        "balance_kwh": 202,
        "settlement_rate": 2.82,
        "added_value_at_settlement": pytest.approx(132 * 2.82),
    }
    assert a["inverter_generation"]["complete"] is True and a["inverter_generation"]["days"] == 25
    _assert_private_absent(bill)

    # re-uploading the same month is flagged and saving replaces it
    again = client.post(f"/api/plants/{pid}/bills/parse", headers=auth, files=files).json()
    assert again["existing_bill_id"] == bill["id"]
    r = client.post(f"/api/plants/{pid}/bills", headers=auth, json={**again["fields"], "import_kwh": 180})
    assert r.status_code == 201 and r.json()["id"] == bill["id"]

    lst = client.get(f"/api/plants/{pid}/bills", headers=auth).json()
    assert len(lst["bills"]) == 1 and lst["bills"][0]["import_kwh"] == 180
    s = lst["summary"]
    assert s["bills"] == 1 and s["total_savings"] == lst["bills"][0]["analysis"]["savings"]
    assert s["bank_balance_kwh"] == 202 and s["bank_value_at_settlement"] == pytest.approx(202 * 2.82)

    # savings to date: the bill's measured figure, plus an estimate only for inverter days outside
    # the bill (2026-09-10..09-19 before it, 10-15..10-19 since the last reading)
    client.patch(f"/api/plants/{pid}", headers=auth, json={"tariff_import_per_kwh": 8.0})
    td = client.get(f"/api/plants/{pid}/bills", headers=auth).json()["summary"]["to_date"]
    assert td["measured"] == s["total_savings"] and td["measured_bills"] == 1
    assert (td["measured_from"], td["measured_to"]) == ("2026-09-20", "2026-10-14")
    assert [(u["start"], u["end"], u["days"]) for u in td["unbilled"]] == [
        ("2026-09-10", "2026-09-19", 10),
        ("2026-10-15", "2026-10-19", 5),
    ]
    assert td["estimated_days"] == 15 and td["estimate_missing_tariff"] is False
    est = sum(u["estimated_savings"] for u in td["unbilled"])
    assert td["estimated"] == pytest.approx(est, abs=0.02)
    assert td["total"] == pytest.approx(td["measured"] + est, abs=0.02)

    # manual entry without a generation meter falls back to the inverter
    manual = {
        "reading_from": "2026-09-19",
        "reading_to": "2026-10-14",
        "bill_month": "2026-09-01",
        "import_kwh": 150,
        "export_kwh": 200,
        "current_bill": 445,
        "fixed_charge": 445,
    }
    r = client.post(f"/api/plants/{pid}/bills", headers=auth, json=manual)
    assert r.status_code == 201
    m = r.json()["analysis"]
    assert m["generation_source"] == "inverter" and m["generation_kwh"] == a["inverter_generation"]["kwh"]
    assert any("Settings" in n or "tariff" in n for n in m["notes"])  # no tariff on a manual bill

    bad = client.put(
        f"/api/plants/{pid}/bills/{r.json()['id']}", headers=auth, json={**manual, "bill_month": "2026-10-01"}
    )
    assert bad.status_code == 409
    assert (
        client.post(
            f"/api/plants/{pid}/bills", headers=auth, json={**manual, "reading_to": "2026-09-01"}
        ).status_code
        == 422
    )
    assert client.delete(f"/api/plants/{pid}/bills/{r.json()['id']}", headers=auth).status_code == 204
    assert len(client.get(f"/api/plants/{pid}/bills", headers=auth).json()["bills"]) == 1


def test_parse_rejects_other_files(client, auth, plant):
    pid = plant["id"]
    r = client.post(
        f"/api/plants/{pid}/bills/parse",
        headers=auth,
        files={"file": ("notes.pdf", b"%PDF-1.4 garbage", "application/pdf")},
    )
    assert r.status_code == 422
