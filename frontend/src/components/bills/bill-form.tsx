"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { BillFields } from "@/lib/api";
import { type BillFormState, toPayload } from "@/lib/bills";

type F = { key: string; label: string; type?: "date" | "month"; hint?: string };

const GROUPS: { title: string; fields: F[] }[] = [
  {
    title: "Dates",
    fields: [
      { key: "bill_month", label: "Billing month", type: "month" },
      { key: "reading_from", label: "Previous reading", type: "date" },
      { key: "reading_to", label: "Current reading", type: "date" },
      { key: "bill_period_months", label: "Bill period (months)", hint: "e.g. 0.83" },
      { key: "bill_date", label: "Bill date", type: "date" },
      { key: "due_date", label: "Due date", type: "date" },
    ],
  },
  {
    title: "Solar net meter (units)",
    fields: [
      { key: "import_kwh", label: "Import" },
      { key: "export_kwh", label: "Export" },
      { key: "generation_kwh", label: "Generation" },
      { key: "billed_kwh", label: "Units billed" },
      { key: "export_offset_kwh", label: "Export offset" },
      { key: "banked_kwh", label: "Bank solar units" },
      { key: "prev_banked_kwh", label: "Previous bank units" },
    ],
  },
  {
    title: "Charges (₹)",
    fields: [
      { key: "fixed_charge", label: "Fixed charge" },
      { key: "energy_charge", label: "Energy charge" },
      { key: "wheeling_charge", label: "Wheeling charge" },
      { key: "fac_charge", label: "Fuel adjustment (FAC)" },
      { key: "electricity_duty", label: "Electricity duty" },
      { key: "tax_on_sale", label: "Tax on sale" },
      { key: "other_charges", label: "Other charges" },
      { key: "current_bill", label: "Current bill total", hint: "before arrears" },
      { key: "amount_payable", label: "Amount payable" },
    ],
  },
];

export function BillForm({
  initial,
  warnings,
  replaces,
  saving,
  onSave,
  onCancel,
}: {
  initial: BillFormState;
  warnings?: string[];
  replaces?: boolean;
  saving: boolean;
  onSave: (payload: BillFields) => void;
  onCancel: () => void;
}) {
  const [s, setS] = useState<BillFormState>(initial);
  const [error, setError] = useState<string | null>(null);
  const setV = (k: string, v: string) => setS((p) => ({ ...p, values: { ...p.values, [k]: v } }));
  const setSlab = (i: number, k: "up_to" | "energy" | "fac", v: string) =>
    setS((p) => ({ ...p, slabs: p.slabs.map((r, j) => (j === i ? { ...r, [k]: v } : r)) }));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const { payload, error: err } = toPayload(s);
    setError(err ?? null);
    if (payload) onSave(payload);
  }

  return (
    <form onSubmit={submit} className="space-y-5">
      {s.source === "pdf" && (
        <p className="text-sm text-muted-foreground">Read from the PDF. Check the numbers against your bill, correct anything wrong, then save.</p>
      )}
      {!!warnings?.length && (
        <ul className="list-disc space-y-0.5 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 pl-7 text-sm">
          {warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
      {replaces && <p className="text-sm text-amber-600 dark:text-amber-400">A bill for this month is already saved; saving replaces it.</p>}

      {GROUPS.map((g) => (
        <fieldset key={g.title} className="space-y-2">
          <legend className="text-sm font-medium">{g.title}</legend>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {g.fields.map((f) => (
              <div key={f.key} className="space-y-1">
                <Label htmlFor={`bill-${f.key}`} className="text-xs">{f.label}</Label>
                <Input
                  id={`bill-${f.key}`}
                  type={f.type ?? "text"}
                  inputMode={f.type ? undefined : "decimal"}
                  placeholder={f.hint}
                  value={s.values[f.key] ?? ""}
                  onChange={(e) => setV(f.key, e.target.value)}
                />
              </div>
            ))}
          </div>
        </fieldset>
      ))}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Tariff (from the bill&apos;s rate table)</legend>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="space-y-1">
            <Label htmlFor="bill-wheeling" className="text-xs">Wheeling (₹/unit)</Label>
            <Input id="bill-wheeling" inputMode="decimal" value={s.wheeling} onChange={(e) => setS((p) => ({ ...p, wheeling: e.target.value }))} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="bill-duty" className="text-xs">Electricity duty (%)</Label>
            <Input id="bill-duty" inputMode="decimal" value={s.duty} onChange={(e) => setS((p) => ({ ...p, duty: e.target.value }))} />
          </div>
          <div className="col-span-2 space-y-1">
            <Label htmlFor="bill-category" className="text-xs">Tariff category</Label>
            <Input id="bill-category" value={s.tariff_category} onChange={(e) => setS((p) => ({ ...p, tariff_category: e.target.value }))} />
          </div>
        </div>
        <div className="space-y-1.5">
          <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 text-xs text-muted-foreground">
            <span>Slab up to (units, blank = above)</span><span>Energy charge (₹/unit)</span><span>FAC (₹/unit)</span><span className="w-9" />
          </div>
          {s.slabs.map((r, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2">
              <Input aria-label={`Slab ${i + 1} up to`} inputMode="decimal" value={r.up_to} onChange={(e) => setSlab(i, "up_to", e.target.value)} />
              <Input aria-label={`Slab ${i + 1} energy rate`} inputMode="decimal" value={r.energy} onChange={(e) => setSlab(i, "energy", e.target.value)} />
              <Input aria-label={`Slab ${i + 1} FAC rate`} inputMode="decimal" value={r.fac} onChange={(e) => setSlab(i, "fac", e.target.value)} />
              <Button type="button" variant="ghost" size="icon" aria-label="Remove slab" onClick={() => setS((p) => ({ ...p, slabs: p.slabs.filter((_, j) => j !== i) }))}>
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button type="button" variant="outline" size="sm" onClick={() => setS((p) => ({ ...p, slabs: [...p.slabs, { up_to: "", energy: "", fac: "" }] }))}>
            <Plus className="mr-1 h-4 w-4" /> Add slab
          </Button>
        </div>
      </fieldset>

      {error && <p className="text-sm text-destructive">{error}</p>}
      <div className="flex gap-2">
        <Button type="submit" disabled={saving}>{saving ? "Saving…" : "Save bill"}</Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={saving}>Cancel</Button>
      </div>
    </form>
  );
}
