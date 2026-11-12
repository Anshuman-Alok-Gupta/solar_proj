"use client";

import { ScanSearch } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, LoadingBlock, PageHeader, SeverityBadge } from "@/components/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { type Anomaly, endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { CATEGORY_LABEL, kwh, longDate, num, pct } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

type Weather = { gti_wh?: number | null; cloud_cover?: number | null; sunshine_h?: number | null; temp_max?: number | null; precip?: number | null };

function Evidence({ a }: { a: Anomaly }) {
  const ev = (a.evidence ?? {}) as { weather?: Weather; neighbouring_days_kwh?: Record<string, number>; z_score?: number | null; model?: { algorithm?: string | null; residual_sigma_kwh?: number | null }; streak_days?: number; intraday?: Record<string, number>; flags?: string[]; explanation?: { method?: string; top_contributions?: { label: string; value: number | null; contribution_kwh: number | null }[] } };
  const w = ev.weather ?? {};
  return (
    <div className="grid gap-4 text-sm md:grid-cols-2">
      <div>
        <div className="mb-1 font-medium">Actual vs expected</div>
        <dl className="grid grid-cols-2 gap-y-0.5">
          <dt className="text-muted-foreground">Actual</dt><dd className="tabular-nums">{kwh(a.actual_kwh)}</dd>
          <dt className="text-muted-foreground">Expected</dt><dd className="tabular-nums">{kwh(a.expected_kwh)}</dd>
          <dt className="text-muted-foreground">Deviation</dt><dd className="tabular-nums">{a.deviation_kwh != null ? `${a.deviation_kwh > 0 ? "+" : ""}${a.deviation_kwh.toFixed(1)} kWh (${pct(a.deviation_pct)})` : "—"}</dd>
          {ev.z_score != null && (<><dt className="text-muted-foreground">z-score</dt><dd className="tabular-nums">{ev.z_score}</dd></>)}
          {ev.model?.algorithm && (<><dt className="text-muted-foreground">Expectation model</dt><dd>{ev.model.algorithm}</dd></>)}
          {ev.streak_days && (<><dt className="text-muted-foreground">Streak</dt><dd>{ev.streak_days} days</dd></>)}
        </dl>
        {ev.neighbouring_days_kwh && (
          <div className="mt-2">
            <div className="text-xs text-muted-foreground">Neighbouring days</div>
            <div className="flex flex-wrap gap-1 pt-1">{Object.entries(ev.neighbouring_days_kwh).map(([d, v]) => <Badge key={d} variant="outline" className="font-normal">{d.slice(5)}: {num(v)}</Badge>)}</div>
          </div>
        )}
      </div>
      <div>
        <div className="mb-1 font-medium">Weather that day (observed)</div>
        <dl className="grid grid-cols-2 gap-y-0.5">
          <dt className="text-muted-foreground">Tilted irradiance</dt><dd className="tabular-nums">{w.gti_wh != null ? `${num(w.gti_wh, 0)} Wh/m²` : "—"}</dd>
          <dt className="text-muted-foreground">Cloud cover</dt><dd className="tabular-nums">{w.cloud_cover != null ? `${num(w.cloud_cover, 0)}%` : "—"}</dd>
          <dt className="text-muted-foreground">Sunshine</dt><dd className="tabular-nums">{w.sunshine_h != null ? `${num(w.sunshine_h, 1)} h` : "—"}</dd>
          <dt className="text-muted-foreground">Max temp</dt><dd className="tabular-nums">{w.temp_max != null ? `${num(w.temp_max, 1)} °C` : "—"}</dd>
          <dt className="text-muted-foreground">Rain</dt><dd className="tabular-nums">{w.precip != null ? `${num(w.precip, 1)} mm` : "—"}</dd>
        </dl>
        {ev.intraday && <p className="mt-2 text-xs text-muted-foreground">Intraday: {ev.intraday.large_swings} large swings in {ev.intraday.daylight_readings} daylight readings (step {ev.intraday.step_minutes} min).</p>}
        {ev.flags && <p className="mt-2 text-xs text-muted-foreground">Flags: {ev.flags.join(", ")}</p>}
        {ev.explanation?.top_contributions && (
          <div className="mt-2 text-xs text-muted-foreground">
            <div>Expectation drivers ({ev.explanation.method}):</div>
            <ul className="list-disc pl-4">{ev.explanation.top_contributions.slice(0, 4).map((c) => <li key={c.label}>{c.label}: {c.value ?? "—"}{c.contribution_kwh != null ? ` (${c.contribution_kwh > 0 ? "+" : ""}${c.contribution_kwh} kWh)` : ""}</li>)}</ul>
          </div>
        )}
      </div>
    </div>
  );
}

export default function AnomaliesPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const [status, setStatus] = useState<string>("open");
  const [severity, setSeverity] = useState<string>("all");
  const [busy, setBusy] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const list = useApi(pid ? () => endpoints.anomalies(pid, { status: status === "all" ? undefined : status, severity: severity === "all" ? undefined : severity, limit: 200 }) : null, [pid, status, severity]);

  if (!plant) return <EmptyState title="No plant selected" />;

  async function detect() {
    if (!pid) return;
    setBusy(true);
    try {
      const r = (await endpoints.detect(pid)) as { created_or_updated: number; by_category: Record<string, number>; notes: string[] };
      toast.success(`Detection complete: ${r.created_or_updated} anomaly record(s)`);
      r.notes?.forEach((n) => toast.message(n));
      await list.refetch();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  async function setAnomalyStatus(a: Anomaly, s: string) {
    if (!pid) return;
    try {
      await endpoints.anomalyStatus(pid, a.id, s);
      await list.refetch();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  return (
    <div>
      <PageHeader
        title="Anomaly Centre"
        description="Weather-adjusted checks on daily generation. Evidence first; never a hardware verdict."
        actions={
          <>
            <Select value={status} onValueChange={(v) => v && setStatus(v)}>
              <SelectTrigger className="w-[150px]" aria-label="Status"><SelectValue /></SelectTrigger>
              <SelectContent>{["open", "acknowledged", "resolved", "all"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
            </Select>
            <Select value={severity} onValueChange={(v) => v && setSeverity(v)}>
              <SelectTrigger className="w-[140px]" aria-label="Severity"><SelectValue /></SelectTrigger>
              <SelectContent>{["all", "high", "medium", "low", "info"].map((s) => <SelectItem key={s} value={s}>{s}</SelectItem>)}</SelectContent>
            </Select>
            <Button onClick={detect} disabled={busy}><ScanSearch className="h-4 w-4" /> {busy ? "Detecting…" : "Run detection"}</Button>
          </>
        }
      />

      {list.error && <ErrorState message={list.error} onRetry={list.refetch} className="mb-4" />}
      {list.data && (
        <div className="mb-4 flex flex-wrap gap-2 text-sm text-muted-foreground">
          <span>{list.data.count} record(s)</span>
          {Object.entries(list.data.by_severity).map(([k, v]) => <Badge key={k} variant="outline" className="capitalize">{k}: {v}</Badge>)}
        </div>
      )}

      {list.loading && !list.data ? <LoadingBlock lines={6} /> : list.data && list.data.items.length === 0 ? (
        <EmptyState title="No anomalies match" description="Run detection after syncing new data. A clean result means no day deviated materially from its weather-adjusted expectation." />
      ) : (
        <div className="space-y-3">
          {list.data?.items.map((a) => {
            const open = openId === a.id;
            return (
              <Card key={a.id} className={cn(open && "ring-1 ring-ring")}>
                <CardContent className="p-4">
                  <button type="button" className="flex w-full flex-col gap-2 text-left sm:flex-row sm:items-center" onClick={() => setOpenId(open ? null : a.id)} aria-expanded={open}>
                    <SeverityBadge severity={a.severity} />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium">{CATEGORY_LABEL[a.category] ?? a.category} <span className="text-muted-foreground">· {longDate(a.date)}</span></div>
                      <div className="text-sm text-muted-foreground">{a.title}</div>
                    </div>
                    <div className="flex items-center gap-2 text-sm tabular-nums">
                      {a.actual_kwh != null && <span>{kwh(a.actual_kwh)}</span>}
                      {a.expected_kwh != null && <span className="text-muted-foreground">vs {kwh(a.expected_kwh)}</span>}
                      <Badge variant="secondary" className="capitalize">{a.status}</Badge>
                    </div>
                  </button>
                  {open && (
                    <div className="mt-4 space-y-4 border-t pt-4">
                      {a.description && <p className="text-sm">{a.description}</p>}
                      <Evidence a={a} />
                      {a.recommended_checks && (
                        <div>
                          <div className="mb-1 text-sm font-medium">Recommended checks</div>
                          <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">{a.recommended_checks.map((c) => <li key={c}>{c}</li>)}</ol>
                        </div>
                      )}
                      <div className="flex flex-wrap gap-2">
                        {a.status !== "acknowledged" && <Button size="sm" variant="outline" onClick={() => setAnomalyStatus(a, "acknowledged")}>Acknowledge</Button>}
                        {a.status !== "resolved" && <Button size="sm" variant="outline" onClick={() => setAnomalyStatus(a, "resolved")}>Mark resolved</Button>}
                        {a.status !== "open" && <Button size="sm" variant="ghost" onClick={() => setAnomalyStatus(a, "open")}>Reopen</Button>}
                        <span className="self-center text-xs text-muted-foreground">detector: {a.detector}</span>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
