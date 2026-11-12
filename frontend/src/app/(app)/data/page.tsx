"use client";

import { CloudDownload, Plug, RefreshCw, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, LoadingBlock, PageHeader, SourceBadge, StatusDot } from "@/components/state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { type ImportJob, type Mapping, type QualityReport, type SyncLog, endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { ago, dateTime, kwh, num, shortDate } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";

function SyncLogTable({ logs }: { logs: SyncLog[] }) {
  if (!logs.length) return <p className="text-sm text-muted-foreground">No synchronisation runs yet.</p>;
  return (
    <Table>
      <TableHeader><TableRow><TableHead>Started</TableHead><TableHead>Mode</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Fetched</TableHead><TableHead className="text-right">New</TableHead><TableHead>Message</TableHead></TableRow></TableHeader>
      <TableBody>
        {logs.map((l) => (
          <TableRow key={l.id}>
            <TableCell className="whitespace-nowrap text-muted-foreground">{dateTime(l.started_at)}</TableCell>
            <TableCell>{l.mode}</TableCell>
            <TableCell><Badge variant={l.status === "success" ? "default" : l.status === "failed" ? "destructive" : "secondary"}>{l.status}</Badge></TableCell>
            <TableCell className="text-right tabular-nums">{l.records_fetched}</TableCell>
            <TableCell className="text-right tabular-nums">{l.records_inserted}</TableCell>
            <TableCell className="max-w-[360px] truncate text-muted-foreground" title={l.error ?? l.message ?? ""}>{l.error ?? l.message}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function QualityView({ q }: { q: QualityReport }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={q.status === "ok" ? "default" : q.status === "warn" ? "secondary" : "destructive"}>quality: {q.status}</Badge>
        <span className="text-muted-foreground">{q.kind} data</span>
        {Object.entries(q.flag_counts).map(([k, v]) => <Badge key={k} variant="outline" className="font-normal">{k}: {v}</Badge>)}
        {q.rejected_count > 0 && <Badge variant="destructive">{q.rejected_count} rejected row(s)</Badge>}
      </div>
      {(q.summary || q.daily_summary) && (
        <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 sm:grid-cols-4">
          {Object.entries(q.summary ?? q.daily_summary ?? {}).map(([k, v]) => (<><dt key={`${k}-k`} className="text-muted-foreground">{k.replace(/_/g, " ")}</dt><dd key={`${k}-v`} className="tabular-nums">{String(v ?? "—")}</dd></>))}
        </dl>
      )}
      <ul className="space-y-0.5">
        {q.checks.map((c) => (
          <li key={c.name} className="flex items-center gap-2"><StatusDot ok={c.status === "ok"} label="" /><span className="font-medium">{c.name.replace(/_/g, " ")}</span><span className="text-muted-foreground">{c.detail}</span>{c.count > 0 && <Badge variant="outline">{c.count}</Badge>}</li>
        ))}
      </ul>
      {q.notes.length > 0 && <ul className="list-disc pl-5 text-muted-foreground">{q.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
      {q.rejected.length > 0 && (
        <details><summary className="cursor-pointer text-muted-foreground">Rejected rows (first {q.rejected.length})</summary>
          <Table><TableBody>{q.rejected.slice(0, 50).map((r) => <TableRow key={r.row}><TableCell>row {r.row}</TableCell><TableCell>{r.reason}</TableCell><TableCell className="font-mono text-xs">{r.value}</TableCell></TableRow>)}</TableBody></Table>
        </details>
      )}
    </div>
  );
}

function ImportWizard({ pid, onDone }: { pid: number; onDone: () => void }) {
  const roles = useApi(() => endpoints.importRoles(pid), [pid]);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [mapping, setMapping] = useState<Mapping | null>(null);
  const [preview, setPreview] = useState<{ kind: string; quality_report: QualityReport; preview_rows: Record<string, unknown>[] } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function upload(file: File) {
    setBusy("upload");
    setPreview(null);
    try {
      const j = await endpoints.importUpload(pid, file);
      setJob(j);
      setMapping(j.inspection?.suggested_mapping ?? { columns: {} });
      if (j.error) toast.error(j.error);
      else toast.success(`Inspected ${j.rows_total} rows (${j.inspection?.granularity.kind} data)`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  function setRole(column: string, role: string) {
    setMapping((m) => {
      const cols = { ...(m?.columns ?? {}) };
      for (const [r, ref] of Object.entries(cols)) if (ref.column === column) delete cols[r];
      if (role !== "ignore") cols[role] = { column, unit: roles.data?.roles.find((x) => x.role === role)?.units?.[0] ?? null };
      return { ...(m ?? {}), columns: cols };
    });
  }
  function setUnit(role: string, unit: string) {
    setMapping((m) => ({ ...(m ?? { columns: {} }), columns: { ...(m?.columns ?? {}), [role]: { ...(m!.columns[role]), unit } } }));
  }

  async function runPreview() {
    if (!job || !mapping) return;
    setBusy("preview");
    try {
      const p = await endpoints.importPreview(pid, job.id, mapping, job.sheet_name);
      setPreview(p);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  async function commit() {
    if (!job) return;
    setBusy("commit");
    try {
      const j = await endpoints.importCommit(pid, job.id);
      toast.success(`Imported: ${j.rows_inserted} new, ${j.rows_updated} updated, ${j.rows_duplicate} duplicates`);
      setJob(null);
      setMapping(null);
      setPreview(null);
      onDone();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const insp = job?.inspection;
  const roleOf = (col: string) => Object.entries(mapping?.columns ?? {}).find(([, ref]) => ref.column === col)?.[0] ?? "ignore";

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Input type="file" accept=".csv,.xlsx,.xls,.txt" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} disabled={busy === "upload"} className="max-w-sm" aria-label="Choose file" />
        {busy === "upload" && <span className="text-sm text-muted-foreground">Inspecting…</span>}
        <p className="text-xs text-muted-foreground">CSV, XLSX or XLS. SEMS “Inverter Operation Report” (5-min) and “Statistical Report” (daily) exports are recognised automatically.</p>
      </div>

      {job && insp && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Step 2 · Confirm column mapping</CardTitle>
            <CardDescription>{job.filename} · {insp.rows} rows · layout {insp.layout} · {insp.granularity.kind}{insp.granularity.median_step_minutes ? ` (${insp.granularity.median_step_minutes} min step)` : ""}{insp.notes.length ? ` · ${insp.notes.join("; ")}` : ""}</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Table>
              <TableHeader><TableRow><TableHead>Column</TableHead><TableHead>Samples</TableHead><TableHead>Range</TableHead><TableHead>Role</TableHead><TableHead>Unit</TableHead></TableRow></TableHeader>
              <TableBody>
                {insp.columns.map((c) => {
                  const role = roleOf(c.name);
                  const units = roles.data?.roles.find((r) => r.role === role)?.units;
                  return (
                    <TableRow key={c.name}>
                      <TableCell className="font-medium">{c.name}</TableCell>
                      <TableCell className="max-w-[220px] truncate text-xs text-muted-foreground">{c.samples.join(", ")}</TableCell>
                      <TableCell className="text-xs tabular-nums text-muted-foreground">{c.min != null ? `${num(c.min, 2)} – ${num(c.max, 2)}` : "—"}</TableCell>
                      <TableCell>
                        <Select value={role} onValueChange={(v) => v && setRole(c.name, v)}>
                          <SelectTrigger className="w-[230px]"><SelectValue /></SelectTrigger>
                          <SelectContent>{(roles.data?.roles ?? []).map((r) => <SelectItem key={r.role} value={r.role}>{r.label}</SelectItem>)}</SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        {units && units.length > 0 ? (
                          <Select value={mapping?.columns[role]?.unit ?? units[0]} onValueChange={(v) => v && setUnit(role, v)}>
                            <SelectTrigger className="w-[100px]"><SelectValue /></SelectTrigger>
                            <SelectContent>{units.map((u) => <SelectItem key={u} value={u}>{u}</SelectItem>)}</SelectContent>
                          </Select>
                        ) : <span className="text-xs text-muted-foreground">—</span>}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
            <div className="flex items-center gap-3">
              <Button onClick={runPreview} disabled={busy !== null}>{busy === "preview" ? "Checking…" : "Validate & preview"}</Button>
              <span className="text-xs text-muted-foreground">Timestamps without a timezone are interpreted in the plant timezone ({job.timezone ?? "plant"}).</span>
            </div>
          </CardContent>
        </Card>
      )}

      {preview && job && (
        <Card>
          <CardHeader><CardTitle className="text-base">Step 3 · Quality report & commit</CardTitle></CardHeader>
          <CardContent className="space-y-4">
            <QualityView q={preview.quality_report} />
            <details>
              <summary className="cursor-pointer text-sm text-muted-foreground">Preview rows ({preview.preview_rows.length})</summary>
              <div className="overflow-x-auto"><Table><TableHeader><TableRow>{Object.keys(preview.preview_rows[0] ?? {}).filter((k) => k !== "quality_flags").map((k) => <TableHead key={k}>{k}</TableHead>)}</TableRow></TableHeader><TableBody>{preview.preview_rows.map((r, i) => <TableRow key={i}>{Object.entries(r).filter(([k]) => k !== "quality_flags").map(([k, v]) => <TableCell key={k} className="text-xs tabular-nums">{v == null ? "—" : String(v)}</TableCell>)}</TableRow>)}</TableBody></Table></div>
            </details>
            <div className="flex items-center gap-3">
              <Button onClick={commit} disabled={busy !== null || preview.quality_report.status === "fail"}>{busy === "commit" ? "Importing…" : "Commit import"}</Button>
              {preview.quality_report.status === "fail" && <span className="text-sm text-destructive">Fix the mapping before committing.</span>}
              {preview.quality_report.status === "warn" && <span className="text-sm text-muted-foreground">Warnings are stored as quality flags with the data; nothing is discarded silently.</span>}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function DataPage() {
  const { plant, capabilities } = useApp();
  const pid = plant?.id ?? null;
  const coverage = useApi(pid ? () => endpoints.coverage(pid) : null, [pid]);
  const sems = useApi(pid ? () => endpoints.semsStatus(pid) : null, [pid]);
  const semsLogs = useApi(pid ? () => endpoints.semsLogs(pid) : null, [pid]);
  const wxLogs = useApi(pid ? () => endpoints.weatherLogs(pid) : null, [pid]);
  const imports = useApi(pid ? () => endpoints.imports(pid) : null, [pid]);
  const [busy, setBusy] = useState<string | null>(null);

  if (!plant) return <EmptyState title="No plant selected" description="Create a plant in Settings first." action={<Button render={<Link href="/settings" />}>Settings</Button>} />;

  const refreshAll = () => Promise.all([coverage.refetch(), sems.refetch(), semsLogs.refetch(), wxLogs.refetch(), imports.refetch()]);

  async function run(name: string, fn: () => Promise<SyncLog | Record<string, unknown>>) {
    setBusy(name);
    try {
      const r = await fn();
      const log = r as SyncLog;
      if (log.status === "failed") toast.error(log.error ?? "Sync failed");
      else toast.success(log.message ?? "Done");
      await refreshAll();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  }

  const st = (sems.data ?? {}) as { configured?: boolean; scheduled?: boolean; interval_seconds?: number; api_kind?: string; last_successful_sync_at?: string | null; has_smart_meter?: boolean | null; latest_reading?: { ts: string; power_w: number | null; age_minutes: number } | null; last_sync?: { status: string; error?: string | null } | null };

  return (
    <div>
      <PageHeader title="Data Management" description="Where the numbers come from: live API, imported files and weather, with synchronisation history and quality checks." />

      <div className="mb-6 grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><Plug className="h-4 w-4" /> GoodWe SEMS</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <StatusDot ok={st.configured ?? null} label={st.configured ? "Credentials configured on server" : "Not configured (set SEMS_ACCOUNT / SEMS_PASSWORD in .env)"} />
            <div className="text-muted-foreground">Last successful sync: {st.last_successful_sync_at ? ago(st.last_successful_sync_at) : "never"}</div>
            {st.last_sync?.status === "failed" && <div className="text-destructive">Last run failed: {st.last_sync.error}</div>}
            <div className="text-muted-foreground">Scheduled polling: {st.scheduled ? `every ${Math.round((st.interval_seconds ?? 0) / 60)} min` : "off (SEMS_SYNC_ENABLED=false)"}</div>
            {st.has_smart_meter === false && <div className="text-xs text-muted-foreground">No smart meter: consumption/export are not measured.</div>}
            <div className="flex flex-wrap gap-2 pt-1">
              <Button size="sm" variant="outline" disabled={!st.configured || busy !== null} onClick={() => run("test", () => endpoints.semsTest(pid!))}>Test connection</Button>
              <Button size="sm" disabled={!st.configured || busy !== null} onClick={() => run("incremental", () => endpoints.semsSync(pid!, "incremental"))}><RefreshCw className={busy === "incremental" ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Sync now</Button>
              <Button size="sm" variant="outline" disabled={!st.configured || busy !== null} onClick={() => run("full", () => endpoints.semsSync(pid!, "full"))}>Full history</Button>
            </div>
            <p className="pt-1 text-xs text-muted-foreground">{st.api_kind}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><CloudDownload className="h-4 w-4" /> Weather (Open-Meteo)</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <StatusDot ok={plant.latitude != null} label={plant.latitude != null ? "Location set" : "Set latitude/longitude in Settings"} />
            <div className="text-muted-foreground">Scheduled: {capabilities?.weather_sync_enabled ? "hourly" : "off"}</div>
            <div className="text-muted-foreground">Last run: {wxLogs.data?.[0] ? `${ago(wxLogs.data[0].started_at)} (${wxLogs.data[0].status})` : "never"}</div>
            <div className="flex flex-wrap gap-2 pt-1">
              <Button size="sm" disabled={plant.latitude == null || busy !== null} onClick={() => run("wx", () => endpoints.weatherSync(pid!, "incremental"))}><RefreshCw className={busy === "wx" ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Sync weather</Button>
              <Button size="sm" variant="outline" disabled={plant.latitude == null || busy !== null} onClick={() => run("wxfull", () => endpoints.weatherSync(pid!, "full"))}>Full history</Button>
            </div>
            <p className="pt-1 text-xs text-muted-foreground">Observed = ERA5 reanalysis (+ archived forecasts for the last few days); Forecast = 7-day model forecast. Stored separately.</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Data coverage</CardTitle></CardHeader>
          <CardContent className="text-sm">
            {coverage.loading && !coverage.data ? <LoadingBlock /> : coverage.error ? <ErrorState message={coverage.error} onRetry={coverage.refetch} /> : coverage.data ? (
              <div className="space-y-2">
                {coverage.data.daily.length === 0 && coverage.data.readings.length === 0 && <p className="text-muted-foreground">No data stored yet.</p>}
                {coverage.data.daily.map((d) => <div key={d.source} className="flex items-center justify-between gap-2"><SourceBadge source={d.source} /><span className="text-muted-foreground">{d.days} days · {shortDate(d.first_date)} → {shortDate(d.last_date)} · {kwh(d.total_kwh, 0)}</span></div>)}
                {coverage.data.readings.map((r) => <div key={r.source} className="flex items-center justify-between gap-2"><SourceBadge source={r.source} /><span className="text-muted-foreground">{r.count} interval readings</span></div>)}
                {!coverage.data.has_real_data && coverage.data.has_any_data && <Alert><AlertTitle>Only synthetic data</AlertTitle><AlertDescription>This plant has demo data only. Connect SEMS or import real exports.</AlertDescription></Alert>}
              </div>
            ) : null}
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="import">
        <TabsList className="mb-4">
          <TabsTrigger value="import">Import CSV / Excel</TabsTrigger>
          <TabsTrigger value="history">Import history</TabsTrigger>
          <TabsTrigger value="sems">SEMS sync log</TabsTrigger>
          <TabsTrigger value="weather">Weather sync log</TabsTrigger>
        </TabsList>
        <TabsContent value="import">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Upload className="h-4 w-4" /> Step 1 · Upload a file</CardTitle></CardHeader>
            <CardContent><ImportWizard pid={pid!} onDone={() => void refreshAll()} /></CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="history">
          <Card><CardContent className="p-4">
            {imports.loading && !imports.data ? <LoadingBlock /> : imports.data?.length ? (
              <Table>
                <TableHeader><TableRow><TableHead>Uploaded</TableHead><TableHead>File</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Rows</TableHead><TableHead className="text-right">Inserted</TableHead><TableHead className="text-right">Updated</TableHead><TableHead>Quality</TableHead><TableHead></TableHead></TableRow></TableHeader>
                <TableBody>
                  {imports.data.map((j) => (
                    <TableRow key={j.id}>
                      <TableCell className="whitespace-nowrap text-muted-foreground">{dateTime(j.created_at)}</TableCell>
                      <TableCell className="max-w-[260px] truncate" title={j.filename}>{j.filename}</TableCell>
                      <TableCell><Badge variant={j.status === "committed" ? "default" : j.status === "failed" ? "destructive" : "secondary"}>{j.status}</Badge></TableCell>
                      <TableCell className="text-right tabular-nums">{j.rows_total ?? "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{j.rows_inserted ?? "—"}</TableCell>
                      <TableCell className="text-right tabular-nums">{j.rows_updated ?? "—"}</TableCell>
                      <TableCell>{j.quality_report?.status ?? (j.error ? <span className="text-destructive">{j.error}</span> : "—")}</TableCell>
                      <TableCell className="text-right"><Button size="icon" variant="ghost" aria-label="Delete import record" onClick={async () => { await endpoints.importDelete(pid!, j.id).catch((e) => toast.error(errorMessage(e))); await imports.refetch(); }}><Trash2 className="h-4 w-4" /></Button></TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : <p className="text-sm text-muted-foreground">No imports yet.</p>}
          </CardContent></Card>
        </TabsContent>
        <TabsContent value="sems"><Card><CardContent className="p-4">{semsLogs.loading && !semsLogs.data ? <LoadingBlock /> : <SyncLogTable logs={semsLogs.data ?? []} />}</CardContent></Card></TabsContent>
        <TabsContent value="weather"><Card><CardContent className="p-4">{wxLogs.loading && !wxLogs.data ? <LoadingBlock /> : <SyncLogTable logs={wxLogs.data ?? []} />}</CardContent></Card></TabsContent>
      </Tabs>
    </div>
  );
}
