import { afterEach, describe, expect, it, vi } from "vitest";

import { endpoints } from "./api";
import { billMonthLabel, toForm, toPayload } from "./bills";

const parsed = {
  bill_month: "2026-10-01",
  bill_date: "2026-10-21",
  reading_from: "2026-09-19",
  reading_to: "2026-10-14",
  bill_period_months: 0.83,
  import_kwh: 170,
  export_kwh: 302,
  generation_kwh: 456,
  current_bill: 445,
  tariff: { slabs: [{ up_to_kwh: 100, energy_rate: 3.96, fac_rate: 0.25 }, { up_to_kwh: null, energy_rate: 10.8, fac_rate: null }], wheeling_per_kwh: 1.6, duty_pct: 16 },
  source: "pdf" as const,
};

describe("bill form", () => {
  it("round-trips parsed fields", () => {
    const form = toForm(parsed);
    expect(form.values.bill_month).toBe("2026-10");
    expect(form.values.import_kwh).toBe("170");
    expect(form.values.energy_charge).toBe("");
    const { payload, error } = toPayload(form);
    expect(error).toBeUndefined();
    expect(payload).toMatchObject({ ...parsed, energy_charge: null, net_metered: true });
  });

  it("requires the readings and rejects non-numbers", () => {
    const form = toForm(parsed);
    form.values.reading_to = "";
    expect(toPayload(form).error).toMatch(/current reading date/);
    form.values.reading_to = "2026-10-14";
    form.values.fixed_charge = "4x5";
    expect(toPayload(form).error).toMatch(/not a number/);
    form.values.fixed_charge = "1,445.50";
    expect(toPayload(form).payload?.fixed_charge).toBe(1445.5);
  });

  it("starts a manual bill with blank MSEDCL slabs or the last bill's tariff", () => {
    const blank = toForm(null);
    expect(blank.slabs.map((s) => s.up_to)).toEqual(["100", "300", "500", "1000", ""]);
    expect(toPayload({ ...blank, values: { ...blank.values, reading_from: "2026-09-19", reading_to: "2026-10-14", import_kwh: "1", export_kwh: "0" } }).payload?.tariff).toBeNull();
    const prefilled = toForm(null, parsed.tariff);
    expect(prefilled.slabs[0]).toEqual({ up_to: "100", energy: "3.96", fac: "0.25" });
    expect(prefilled.source).toBe("manual");
  });

  it("labels billing months", () => {
    expect(billMonthLabel("2026-10-01")).toBe("Oct 2026");
    expect(billMonthLabel(null)).toBe("—");
  });
});

describe("bill upload", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("never sends the real filename (it contains the consumer number)", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ fields: {}, warnings: [], existing_bill_id: null }), { status: 200, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await endpoints.billParse(3, new File(["%PDF-1.4"], "EB2610000099998888777.pdf", { type: "application/pdf" }));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/plants\/3\/bills\/parse$/);
    const file = (init.body as FormData).get("file") as File;
    expect(file.name).toBe("bill.pdf");
  });
});
