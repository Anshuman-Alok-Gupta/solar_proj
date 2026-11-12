import type { TariffSlab } from "./api";

/** Settings form rows -> API payload. Rows without a rate are ignored; empty "up to" = open-ended. */
export function slabsPayload(rows: { up_to: string; rate: string }[]): TariffSlab[] | null {
  const out = rows
    .filter((r) => r.rate.trim() !== "")
    .map((r) => ({ up_to_kwh: r.up_to.trim() === "" ? null : Number(r.up_to), rate: Number(r.rate) }));
  return out.length ? out : null;
}

/** Energy charge for `units` in one month (mirrors backend app/services/tariff.py). */
export function slabCost(units: number, slabs: TariffSlab[]): number {
  if (units <= 0 || !slabs.length) return 0;
  let cost = 0;
  let prev = 0;
  for (const s of slabs) {
    if (s.up_to_kwh === null || units <= s.up_to_kwh) return cost + (units - prev) * s.rate;
    cost += (s.up_to_kwh - prev) * s.rate;
    prev = s.up_to_kwh;
  }
  return cost + (units - prev) * slabs[slabs.length - 1].rate;
}
