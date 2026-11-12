import { describe, expect, it } from "vitest";

import { kwh, longDate, money, num, pct, shortDate, watts } from "./format";

describe("format helpers", () => {
  it("renders missing values as an em dash instead of 0 or NaN", () => {
    for (const fn of [kwh, num, pct, watts]) {
      expect(fn(null)).toBe("—");
      expect(fn(undefined)).toBe("—");
    }
    expect(kwh(Number.NaN)).toBe("—");
    expect(money(null)).toBe("—");
    expect(shortDate(null)).toBe("—");
    expect(longDate(undefined)).toBe("—");
  });

  it("formats energy, power and percentages", () => {
    expect(kwh(14.893)).toBe("14.9 kWh");
    expect(kwh(14.893, 2)).toBe("14.89 kWh");
    expect(kwh(0)).toBe("0.0 kWh");
    expect(watts(850)).toBe("850 W");
    expect(watts(4321)).toBe("4.32 kW");
    expect(pct(12.4)).toBe("+12%");
    expect(pct(-3.25, 1)).toBe("-3.3%");
    expect(pct(0)).toBe("0%");
  });

  it("formats dates from ISO strings and passes through garbage", () => {
    expect(shortDate("2026-10-22")).toBe("22 Oct");
    expect(longDate("2026-10-22")).toBe("Thu 22 Oct 2026");
    expect(shortDate("not a date")).toBe("not a date");
  });

  it("formats money with the plant currency and survives unknown codes", () => {
    expect(money(1234.4, "INR")).toMatch(/1,234/);
    expect(money(50, "NOT-A-CODE")).toBe("NOT-A-CODE 50");
  });
});
