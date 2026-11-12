"use client";

import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, PageHeader } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { type Plant, endpoints } from "@/lib/api";
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
  inverter_manufacturer: string;
  inverter_model: string;
  monitoring_platform: string;
  sems_station_id: string;
  notes: string;
};

const EMPTY: Form = { name: "", capacity_kw: "", latitude: "", longitude: "", timezone: "UTC", panel_tilt_deg: "", panel_azimuth_deg: "", installation_date: "", installation_cost: "", currency: "INR", tariff_import_per_kwh: "", tariff_export_per_kwh: "", inverter_manufacturer: "", inverter_model: "", monitoring_platform: "", sems_station_id: "", notes: "" };

function fromPlant(p: Plant): Form {
  const s = (v: unknown) => (v == null ? "" : String(v));
  return { name: p.name, capacity_kw: s(p.capacity_kw), latitude: s(p.latitude), longitude: s(p.longitude), timezone: p.timezone, panel_tilt_deg: s(p.panel_tilt_deg), panel_azimuth_deg: s(p.panel_azimuth_deg), installation_date: s(p.installation_date), installation_cost: s(p.installation_cost), currency: p.currency, tariff_import_per_kwh: s(p.tariff_import_per_kwh), tariff_export_per_kwh: s(p.tariff_export_per_kwh), inverter_manufacturer: s(p.inverter_manufacturer), inverter_model: s(p.inverter_model), monitoring_platform: s(p.monitoring_platform), sems_station_id: s(p.sems_station_id), notes: s(p.notes) };
}

function toPayload(f: Form): Partial<Plant> {
  const n = (v: string) => (v.trim() === "" ? null : Number(v));
  const t = (v: string) => (v.trim() === "" ? null : v.trim());
  return { name: f.name.trim(), capacity_kw: n(f.capacity_kw), latitude: n(f.latitude), longitude: n(f.longitude), timezone: f.timezone.trim() || "UTC", panel_tilt_deg: n(f.panel_tilt_deg), panel_azimuth_deg: n(f.panel_azimuth_deg), installation_date: t(f.installation_date), installation_cost: n(f.installation_cost), currency: f.currency.trim() || "INR", tariff_import_per_kwh: n(f.tariff_import_per_kwh), tariff_export_per_kwh: n(f.tariff_export_per_kwh), inverter_manufacturer: t(f.inverter_manufacturer), inverter_model: t(f.inverter_model), monitoring_platform: t(f.monitoring_platform), sems_station_id: t(f.sems_station_id), notes: t(f.notes) };
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
  const [form, setForm] = useState<Form>(() => (plant && mode === "edit" ? fromPlant(plant) : EMPTY));
  const [busy, setBusy] = useState(false);

  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => setForm((f) => ({ ...f, [k]: e.target.value }));

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
          <CardHeader><CardTitle className="text-base">Tariff & economics</CardTitle><CardDescription>Savings are estimates. Without a smart meter the app shows a range between all-exported and all-self-consumed.</CardDescription></CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field id="cur" label="Currency"><Input id="cur" value={form.currency} onChange={set("currency")} /></Field>
            <Field id="imp" label="Import tariff (per kWh)" hint="What you pay the utility"><Input id="imp" type="number" step="0.01" min="0" value={form.tariff_import_per_kwh} onChange={set("tariff_import_per_kwh")} /></Field>
            <Field id="exp" label="Export / net-metering rate (per kWh)" hint="Leave empty to assume the import tariff"><Input id="exp" type="number" step="0.01" min="0" value={form.tariff_export_per_kwh} onChange={set("tariff_export_per_kwh")} /></Field>
            <Field id="cost" label="Installation cost" hint="Enables payback estimate"><Input id="cost" type="number" step="1" min="0" value={form.installation_cost} onChange={set("installation_cost")} /></Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="text-base">Notes</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <Textarea rows={5} value={form.notes} onChange={set("notes")} aria-label="Notes" />
            <Button type="submit" disabled={busy}>{busy ? "Saving…" : mode === "create" ? "Create plant" : "Save settings"}</Button>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}
