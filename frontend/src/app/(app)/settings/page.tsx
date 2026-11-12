"use client";

import { useState } from "react";
import { toast } from "sonner";

import { AccountCard, SharingCard } from "@/components/settings/account-sharing";
import { EmptyState, PageHeader } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { type Plant, endpoints } from "@/lib/api";
import { slabsPayload } from "@/lib/tariff";
import { useApp } from "@/lib/app-context";
import { errorMessage } from "@/lib/use-api";

type Form = {
  name: string;
  capacity_kw: string;
  latitude: string;
  longitude: string;
  timezone: string;
  panel_tilt_deg: string;
  panel_azimuth_deg: string;
  installation_date: string;
  installation_cost: string;
  currency: string;
  tariff_import_per_kwh: string;
  tariff_export_per_kwh: string;
  monthly_consumption_kwh: string;
  slabs: { up_to: string; rate: string }[];
  inverter_manufacturer: string;
  inverter_model: string;
  monitoring_platform: string;
  sems_station_id: string;
  notes: string;
};

const EMPTY: Form = { name: "", capacity_kw: "", latitude: "", longitude: "", timezone: "UTC", panel_tilt_deg: "", panel_azimuth_deg: "", installation_date: "", installation_cost: "", currency: "INR", tariff_import_per_kwh: "", tariff_export_per_kwh: "", monthly_consumption_kwh: "", slabs: [], inverter_manufacturer: "", inverter_model: "", monitoring_platform: "", sems_station_id: "", notes: "" };

function fromPlant(p: Plant): Form {
  const s = (v: unknown) => (v == null ? "" : String(v));
  return { name: p.name, capacity_kw: s(p.capacity_kw), latitude: s(p.latitude), longitude: s(p.longitude), timezone: p.timezone, panel_tilt_deg: s(p.panel_tilt_deg), panel_azimuth_deg: s(p.panel_azimuth_deg), installation_date: s(p.installation_date), installation_cost: s(p.installation_cost), currency: p.currency, tariff_import_per_kwh: s(p.tariff_import_per_kwh), tariff_export_per_kwh: s(p.tariff_export_per_kwh), monthly_consumption_kwh: s(p.monthly_consumption_kwh), slabs: (p.tariff_slabs ?? []).map((x) => ({ up_to: s(x.up_to_kwh), rate: s(x.rate) })), inverter_manufacturer: s(p.inverter_manufacturer), inverter_model: s(p.inverter_model), monitoring_platform: s(p.monitoring_platform), sems_station_id: s(p.sems_station_id), notes: s(p.notes) };
}

function toPayload(f: Form): Partial<Plant> {
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const t = (v: string) => (v.trim() === "" ? null : v.trim());
  return { name: f.name.trim(), capacity_kw: n(f.capacity_kw), latitude: n(f.latitude), longitude: n(f.longitude), timezone: f.timezone.trim() || "UTC", panel_tilt_deg: n(f.panel_tilt_deg), panel_azimuth_deg: n(f.panel_azimuth_deg), installation_date: t(f.installation_date), installation_cost: n(f.installation_cost), currency: f.currency.trim() || "INR", tariff_import_per_kwh: n(f.tariff_import_per_kwh), tariff_export_per_kwh: n(f.tariff_export_per_kwh), monthly_consumption_kwh: n(f.monthly_consumption_kwh), tariff_slabs: slabsPayload(f.slabs), inverter_manufacturer: t(f.inverter_manufacturer), inverter_model: t(f.inverter_model), monitoring_platform: t(f.monitoring_platform), sems_station_id: t(f.sems_station_id), notes: t(f.notes) };
}

function Field({ id, label, hint, children }: { id: string; label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export default function SettingsPage() {
  const { plant } = useApp();
  const [mode, setMode] = useState<"edit" | "create">(plant ? "edit" : "create");
  // Remount the form whenever the plant or mode changes so initial values are derived, not synced.
  return <SettingsForm key={`${plant?.id ?? "none"}-${mode}`} mode={mode} setMode={setMode} />;
}

function SettingsForm({ mode, setMode }: { mode: "edit" | "create"; setMode: (m: "edit" | "create") => void }) {
  const { plant, refreshPlants, selectPlant, capabilities } = useApp();
  const readOnly = mode === "edit" && plant?.access_role === "viewer";
  const [form, setForm] = useState<Form>(() => (plant && mode === "edit" ? fromPlant(plant) : EMPTY));
  const [busy, setBusy] = useState(false);

  const setSlab = (i: number, k: "up_to" | "rate", v: string) => setForm((f) => ({ ...f, slabs: f.slabs.map((r, j) => (j === i ? { ...r, [k]: v } : r)) }));
  const set = (k: Exclude<keyof Form, "slabs">) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "create" || !plant) {
        const p = await endpoints.createPlant(toPayload(form));
        await refreshPlants();
        selectPlant(p.id);
        setMode("edit");
        toast.success("Plant created");
      } else {
        await endpoints.updatePlant(plant.id, toPayload(form));
        await refreshPlants();
        toast.success("Settings saved");
      }
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="Settings" description="Plant configuration used by weather alignment, yield metrics, savings estimates and the AI analyst." actions={plant && mode === "edit" ? <Button variant="outline" size="sm" onClick={() => setMode("create")}>Add another plant</Button> : plant ? <Button variant="ghost" size="sm" onClick={() => setMode("edit")}>Cancel</Button> : null} />

      {!plant && mode === "edit" && <EmptyState title="No plant yet" description="Create one below." className="mb-4" />}

      <form onSubmit={save} className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="text-base">{mode === "create" ? "New plant" : "Plant"}</CardTitle><CardDescription>Capacity and timezone drive the daily aggregation and yield figures.</CardDescription></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field id="name" label="Name"><Input id="name" required value={form.name} onChange={set("name")} /></Field>
            <Field id="capacity" label="PV capacity (kW DC)"><Input id="capacity" type="number" step="0.1" min="0" value={form.capacity_kw} onChange={set("capacity_kw")} /></Field>
            <Field id="tz" label="Timezone" hint="IANA name, e.g. Asia/Kolkata"><Input id="tz" value={form.timezone} onChange={set("timezone")} /></Field>
            <Field id="installed" label="Installation / first-generation date"><Input id="installed" type="date" value={form.installation_date} onChange={set("installation_date")} /></Field>
            <Field id="lat" label="Latitude" hint="Used only for weather retrieval; never shown publicly"><Input id="lat" type="number" step="any" value={form.latitude} onChange={set("latitude")} /></Field>
            <Field id="lon" label="Longitude"><Input id="lon" type="number" step="any" value={form.longitude} onChange={set("longitude")} /></Field>
            <Field id="tilt" label="Panel tilt (°)" hint="0 = flat"><Input id="tilt" type="number" step="1" min="0" max="90" value={form.panel_tilt_deg} onChange={set("panel_tilt_deg")} /></Field>
            <Field id="az" label="Panel azimuth (°)" hint="0 = south, 90 = west, −90 = east (southwest = 45)"><Input id="az" type="number" step="1" min="-180" max="180" value={form.panel_azimuth_deg} onChange={set("panel_azimuth_deg")} /></Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Inverter & monitoring</CardTitle></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field id="man" label="Inverter manufacturer"><Input id="man" value={form.inverter_manufacturer} onChange={set("inverter_manufacturer")} /></Field>
            <Field id="model" label="Inverter model"><Input id="model" value={form.inverter_model} onChange={set("inverter_model")} /></Field>
            <Field id="platform" label="Monitoring platform"><Input id="platform" value={form.monitoring_platform} onChange={set("monitoring_platform")} /></Field>
            <Field id="station" label="SEMS station id" hint="Leave empty to auto-discover on single-station accounts"><Input id="station" value={form.sems_station_id} onChange={set("sems_station_id")} /></Field>
            <div className="sm:col-span-2 text-xs text-muted-foreground">SEMS credentials are server-side only (.env). Status: {capabilities?.sems_configured ? "configured" : "not configured"}.</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Tariff & economics</CardTitle><CardDescription>Savings are estimates. With net metering (e.g. MSEB/MSEDCL) every generated unit offsets a unit you would have bought; surplus units still banked at the end of the financial year are paid at the settlement rate. Without a smart meter the app shows the range between the two.</CardDescription></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field id="cur" label="Currency"><Input id="cur" value={form.currency} onChange={set("currency")} /></Field>
            <Field id="imp" label="Flat import tariff (per kWh)" hint="Only if your bill has one rate; leave empty when you use slabs below"><Input id="imp" type="number" step="0.01" min="0" value={form.tariff_import_per_kwh} onChange={set("tariff_import_per_kwh")} /></Field>
            <Field id="exp" label="Surplus settlement rate (per kWh)" hint="What the utility pays for units left at financial-year end. Empty = import tariff"><Input id="exp" type="number" step="0.01" min="0" value={form.tariff_export_per_kwh} onChange={set("tariff_export_per_kwh")} /></Field>
            <Field id="cost" label="Installation cost" hint="Enables payback estimate"><Input id="cost" type="number" step="1" min="0" value={form.installation_cost} onChange={set("installation_cost")} /></Field>
            <Field id="cons" label="Household use (units / month)" hint="Typical monthly units before solar, from an old bill. Needed with slab tariffs"><Input id="cons" type="number" step="1" min="0" value={form.monthly_consumption_kwh} onChange={set("monthly_consumption_kwh")} /></Field>
            <div className="space-y-2 sm:col-span-2">
              <Label>Slab tariff (per month)</Label>
              <p className="text-xs text-muted-foreground">Energy charge per unit for each slab, e.g. MSEB residential. Leave “up to” empty on the last slab. Solar units come off the top slabs first.</p>
              {form.slabs.map((r, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-20 shrink-0 text-xs text-muted-foreground tabular-nums">{i === 0 ? "0" : form.slabs[i - 1].up_to || "?"} –</span>
                  <Input aria-label={`Slab ${i + 1} up to (units)`} type="number" step="1" min="1" placeholder="no limit" value={r.up_to} onChange={(e) => setSlab(i, "up_to", e.target.value)} className="w-28" />
                  <span className="text-xs text-muted-foreground">units @</span>
                  <Input aria-label={`Slab ${i + 1} rate`} type="number" step="0.01" min="0" placeholder="rate" value={r.rate} onChange={(e) => setSlab(i, "rate", e.target.value)} className="w-24" />
                  <Button type="button" size="sm" variant="ghost" onClick={() => setForm((f) => ({ ...f, slabs: f.slabs.filter((_, j) => j !== i) }))} aria-label={`Remove slab ${i + 1}`}>Remove</Button>
                </div>
              ))}
              <Button type="button" size="sm" variant="outline" onClick={() => setForm((f) => ({ ...f, slabs: [...f.slabs, { up_to: "", rate: "" }] }))}>Add slab</Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Notes</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <Textarea rows={5} value={form.notes} onChange={set("notes")} aria-label="Notes" />
            <Button type="submit" disabled={busy || readOnly}>{busy ? "Saving…" : mode === "create" ? "Create plant" : "Save settings"}</Button>
            {readOnly && <p className="text-xs text-muted-foreground">This plant is shared with you as a viewer, so settings are read-only.</p>}
          </CardContent>
        </Card>
      </form>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {plant && mode === "edit" && (plant.access_role ?? "owner") === "owner" && <SharingCard plant={plant} />}
        <AccountCard />
      </div>
    </div>
  );
}
