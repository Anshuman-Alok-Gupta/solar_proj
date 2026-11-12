"use client";

import { Download, FileText, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, LoadingBlock, PageHeader } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type Report, endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { kwh, longDate, money, pct } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";

type Kind = "daily" | "weekly" | "monthly";
const KINDS: { value: Kind; label: string }[] = [
  { value: "daily", label: "Daily (yesterday)" },
  { value: "weekly", label: "Weekly (this week)" },
  { value: "monthly", label: "Monthly (this month)" },
];

type Gen = { total_kwh?: number; avg_daily_kwh?: number | null; specific_yield_kwh_per_kw?: number | null; change_vs_previous_pct?: number | null; best_day?: { date: string; kwh: number } | null };
type Savings = { currency?: string; estimated_value?: number | null };

function Figures({ r }: { r: Report }) {
  const c = r.content as { generation?: Gen; anomalies?: unknown[]; savings?: Savings; period?: { complete_days?: number } };
  const g = c.generation ?? {};
  const items: [string, string][] = [
    ["Total", kwh(g.total_kwh ?? null, 1)],
    ["Per day", kwh(g.avg_daily_kwh ?? null, 2)],
    ["vs previous", pct(g.change_vs_previous_pct ?? null, 1)],
    ["Best day", g.best_day ? `${g.best_day.date.slice(5)} · ${kwh(g.best_day.kwh)}` : "—"],
    ["Anomalies", String(c.anomalies?.length ?? 0)],
    ["Value (est.)", c.savings?.estimated_value != null ? money(c.savings.estimated_value, c.savings.currency) : "not set"],
  ];
  return (
    <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {items.map(([k, v]) => (
        <div key={k} className="rounded-md border p-3">
          <dt className="text-xs text-muted-foreground">{k}</dt>
          <dd className="mt-1 font-medium tabular-nums">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export default function ReportsPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const canWrite = (plant?.access_role ?? "owner") !== "viewer";
  const [kind, setKind] = useState<Kind>("monthly");
  const preview = useApi(pid ? () => endpoints.reportPreview(pid, kind) : null, [pid, kind]);
  const saved = useApi(pid ? () => endpoints.reports(pid) : null, [pid]);
  const [busy, setBusy] = useState<string | null>(null);

  if (!plant || pid === null) return <EmptyState title="No plant selected" description="Create a plant in Settings first." />;

  async function act(key: string, fn: () => Promise<unknown>, ok?: string) {
    setBusy(key);
    try {
      await fn();
      if (ok) toast.success(ok);
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const generate = () =>
    act(
      "generate",
      async () => {
        const r = await endpoints.reportGenerate(pid, kind);
        await saved.refetch();
        if (r.id) await endpoints.reportDownload(pid, r.id, "pdf");
      },
      "Report saved and downloaded",
    );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Reports"
        description="Performance reports built only from stored data: measured generation, weather, anomalies, forecasts and (estimated) value. Download as PDF or CSV."
        actions={
          <>
            <Select value={kind} onValueChange={(v) => v && setKind(v as Kind)}>
              <SelectTrigger className="w-[200px]" aria-label="Report period">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {KINDS.map((k) => (
                  <SelectItem key={k.value} value={k.value}>{k.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {canWrite && (
              <Button onClick={generate} disabled={busy !== null || !preview.data}>
                <FileText className="h-4 w-4" /> {busy === "generate" ? "Generating…" : "Save & download PDF"}
              </Button>
            )}
          </>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{preview.data?.title ?? "Preview"}</CardTitle>
          <CardDescription>Live preview; nothing is saved until you generate the report.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {preview.loading && !preview.data ? (
            <LoadingBlock lines={4} />
          ) : preview.error ? (
            <ErrorState message={preview.error} onRetry={preview.refetch} />
          ) : preview.data ? (
            <>
              <Figures r={preview.data} />
              <p className="text-sm leading-relaxed text-muted-foreground">{preview.data.summary_text}</p>
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Saved reports</CardTitle>
        </CardHeader>
        <CardContent>
          {saved.loading && !saved.data ? (
            <LoadingBlock lines={3} />
          ) : saved.error ? (
            <ErrorState message={saved.error} onRetry={saved.refetch} />
          ) : !saved.data?.items.length ? (
            <EmptyState icon={<FileText className="h-8 w-8" />} title="No saved reports yet" description={canWrite ? "Pick a period above and choose “Save & download PDF”." : "The plant owner hasn't saved any reports yet."} />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Report</TableHead>
                  <TableHead className="hidden sm:table-cell">Created</TableHead>
                  <TableHead className="text-right">Download</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {saved.data.items.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="max-w-[320px] truncate" title={r.title}>{r.title}</TableCell>
                    <TableCell className="hidden text-muted-foreground sm:table-cell">{r.created_at ? longDate(r.created_at.slice(0, 10)) : "—"}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => act(`pdf-${r.id}`, () => endpoints.reportDownload(pid, r.id!, "pdf"))}>
                          <Download className="h-3.5 w-3.5" /> PDF
                        </Button>
                        <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => act(`csv-${r.id}`, () => endpoints.reportDownload(pid, r.id!, "csv"))}>
                          CSV
                        </Button>
                        {canWrite && (
                          <Button size="icon" variant="ghost" aria-label="Delete report" disabled={busy !== null} onClick={() => act(`del-${r.id}`, async () => { await endpoints.reportDelete(pid, r.id!); await saved.refetch(); }, "Report deleted")}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
