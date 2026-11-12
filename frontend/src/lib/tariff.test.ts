import { describe, expect, it } from "vitest";

import { slabCost, slabsPayload } from "./tariff";

const MSEB = [
  { up_to_kwh: 100, rate: 4.28 },
  { up_to_kwh: 300, rate: 11.1 },
  { up_to_kwh: 500, rate: 15.38 },
  { up_to_kwh: 1000, rate: 17.68 },
  { up_to_kwh: null, rate: 17.68 },
];

describe("slab tariff helpers", () => {
  it("turns settings rows into the API payload", () => {
    expect(slabsPayload([{ up_to: "100", rate: "4.28" }, { up_to: "", rate: "11.10" }, { up_to: "500", rate: "" }])).toEqual([
      { up_to_kwh: 100, rate: 4.28 },
      { up_to_kwh: null, rate: 11.1 },
    ]);
    expect(slabsPayload([])).toBeNull();
  });

  it("matches the backend slab arithmetic", () => {
    expect(slabCost(0, MSEB)).toBe(0);
    expect(slabCost(400, MSEB)).toBeCloseTo(4186, 6);
    expect(slabCost(400, MSEB) - slabCost(200, MSEB)).toBeCloseTo(1538 + 1110, 6);
  });
});
