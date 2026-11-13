import type { BillFields, BillTariff } from "./api";

export const NUMBER_FIELDS = [
  "bill_period_months",
  "sanctioned_load_kw",
  "import_kwh",
  "export_kwh",
  "generation_kwh",
  "billed_kwh",
  "export_offset_kwh",
  "banked_kwh",
  "prev_banked_kwh",
  "fixed_charge",
  "energy_charge",
  "wheeling_charge",
  "fac_charge",
  "electricity_duty",
  "tax_on_sale",
  "other_charges",
  "current_bill",
  "amount_payable",
] as const;
export const DATE_FIELDS = ["bill_date", "due_date", "reading_from", "reading_to"] as const;
const REQUIRED: Record<string, string> = {
  reading_from: "previous reading date",
  reading_to: "current reading date",
  import_kwh: "import units",
  export_kwh: "export units",
};

export type SlabRow = { up_to: string; energy: string; fac: string };

export interface BillFormState {
  values: Record<string, string>;
  slabs: SlabRow[];
  wheeling: string;
  duty: string;
  tariff_category: string;
  net_metered: boolean;
  source: "pdf" | "manual";
}

const str = (v: number | string | null | undefined) => (v === null || v === undefined ? "" : String(v));

/** MSEDCL residential slab limits, rates left blank. */
export const BLANK_SLABS: SlabRow[] = ["100", "300", "500", "1000", ""].map((up_to) => ({ up_to, energy: "", fac: "" }));

/** API fields (parsed PDF, saved bill or nothing) -> form strings. `tariff` pre-fills a manual entry. */
export function toForm(f?: Partial<BillFields> | null, tariff?: BillTariff | null): BillFormState {
  const values: Record<string, string> = {};
  for (const k of [...NUMBER_FIELDS, ...DATE_FIELDS]) values[k] = str(f?.[k] as number | string | null | undefined);
  values.bill_month = (f?.bill_month ?? "").slice(0, 7);
  const t = f?.tariff ?? tariff ?? null;
  return {
    values,
    slabs: t?.slabs?.length
      ? t.slabs.map((s) => ({ up_to: str(s.up_to_kwh), energy: str(s.energy_rate), fac: str(s.fac_rate) }))
      : BLANK_SLABS.map((s) => ({ ...s })),
    wheeling: str(t?.wheeling_per_kwh),
    duty: str(t?.duty_pct),
    tariff_category: f?.tariff_category ?? "",
    net_metered: f?.net_metered ?? true,
    source: f?.source ?? "manual",
  };
}

function parseNum(raw: string): number | null | "bad" {
  const t = raw.replace(/,/g, "").trim();
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : "bad";
}

/** Form strings -> API payload, or the first problem found. */
export function toPayload(s: BillFormState): { payload?: BillFields; error?: string } {
  for (const [k, label] of Object.entries(REQUIRED)) if (!s.values[k]?.trim()) return { error: `Enter the ${label}.` };
  const out: Record<string, unknown> = {};
  for (const k of NUMBER_FIELDS) {
    const v = parseNum(s.values[k] ?? "");
    if (v === "bad") return { error: `"${s.values[k]}" is not a number (${k.replace(/_/g, " ")}).` };
    out[k] = v;
  }
  for (const k of DATE_FIELDS) out[k] = s.values[k]?.trim() || null;
  out.bill_month = s.values.bill_month ? `${s.values.bill_month.slice(0, 7)}-01` : null;

  const slabs = [];
  for (const r of s.slabs) {
    if (!r.energy.trim()) continue;
    const up = parseNum(r.up_to);
    const energy = parseNum(r.energy);
    const fac = parseNum(r.fac);
    if (up === "bad" || energy === "bad" || fac === "bad" || energy === null) return { error: "Check the tariff slab rates." };
    slabs.push({ up_to_kwh: up, energy_rate: energy, fac_rate: fac });
  }
  const wheeling = parseNum(s.wheeling);
  const duty = parseNum(s.duty);
  if (wheeling === "bad" || duty === "bad") return { error: "Check the wheeling and duty rates." };
  out.tariff = slabs.length || wheeling !== null || duty !== null ? { slabs, wheeling_per_kwh: wheeling, duty_pct: duty } : null;
  out.tariff_category = s.tariff_category.trim() || null;
  out.net_metered = s.net_metered;
  out.source = s.source;
  return { payload: out as unknown as BillFields };
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-01" -> "Oct 2026" */
export function billMonthLabel(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m] = iso.split("-");
  return `${MONTHS[Number(m) - 1] ?? m} ${y}`;
}
