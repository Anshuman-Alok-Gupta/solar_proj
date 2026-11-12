"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";

import { ChartCard } from "@/components/charts/common";
import { DailyGenerationChart, ForecastChart } from "@/components/charts/generation-charts";
import { ChartSkeleton, EmptyState, ErrorState, PageHeader } from "@/components/state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { ALGO_LABEL, ago, kwh, longDate, shortDate } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";

export default function ForecastingPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const [busy, setBusy] = useState(false);
  const forecasts = useApi(pid ? () => endpoints.forecasts(pid, 60) : null, [pid]);
  const backtest = useApi(pid ? () => endpoints.backtest(pid) : null, [pid]);
  const models = useApi(pid ? () => endpoints.models(pid) : null, [pid]);
  const daily = useApi(pid ? () => endpoints.daily(pid, { start: new Date(Date.now() - 60 * 86400000).toISOString().slice(0, 10) }) : null, [pid]);

  if (!plant) return <EmptyState title="No plant selected" />;
  const active = models.data?.items.find((m) => m.id === models.data?.active_model_id) ?? null;
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = (forecasts.data?.items ?? []).filter((f) => f.target_date > today).sort((a, b) => a.target_date.localeCompare(b.target_date));
  const scored = (forecasts.data?.items ?? []).filter((f) => f.actual_kwh != null);
  const expectedMap = Object.fromEntries((backtest.data?.items ?? []).map((i) => [i.date, i.expected_kwh]));

  async function issue() {
    if (!pid) return;
    setBusy(true);
    try {
      const res = await endpoints.issueForecast(pid, 3);
      toast.success(`Issued ${res.issued.length} forecast(s)`);
      res.notes.forEach((n) => toast.message(n));
      await forecasts.refetch();
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader
        title="Forecasting"
        description="Next-day generation predicted from the Open-Meteo weather forecast and recent production."
        actions={
          <Button onClick={issue} disabled={busy || !active}>
            <RefreshCw className={busy ? "h-4 w-4 animate-spin" : "h-4 w-4"} /> Issue forecast (3 days)
          </Button>
        }
      />

      {!models.loading && !active && (
        <Alert className="mb-4">
          <AlertTitle>No active model</AlertTitle>
          <AlertDescription>
            Train a model in the <Link className="underline" href="/model-lab">Model Lab</Link> first. Forecasts are only produced from a trained, evaluated model.
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader><CardTitle className="text-base">Upcoming</CardTitle></CardHeader>
          <CardContent>
            {forecasts.loading && !forecasts.data ? <ChartSkeleton height={120} /> : upcoming.length ? (
              <ul className="space-y-3">
                {upcoming.map((f) => (
                  <li key={f.target_date} className="rounded-md border p-3">
                    <div className="text-xs text-muted-foreground">{longDate(f.target_date)} · +{f.horizon_days}d</div>
                    <div className="text-2xl font-semibold tabular-nums">{kwh(f.predicted_kwh)}</div>
                    {f.lower_kwh != null && f.upper_kwh != null && <div className="text-xs text-muted-foreground">10–90% interval {f.lower_kwh.toFixed(1)}–{f.upper_kwh.toFixed(1)} kWh</div>}
                    <div className="text-xs text-muted-foreground">issued {ago(f.issued_at)} · {f.weather_input_kind} weather</div>
                  </li>
                ))}
              </ul>
            ) : <EmptyState title="No upcoming forecast" description="Issue a forecast to predict the next days." className="p-4" />}
            {active && <p className="mt-3 text-xs text-muted-foreground">Model: {ALGO_LABEL[active.algorithm] ?? active.algorithm} · validation MAE {active.metrics?.validation?.mae ?? "—"} kWh</p>}
          </CardContent>
        </Card>

        <ChartCard title="Forecast vs actual" description="Forecasts issued with forecast weather, scored against measured generation" className="lg:col-span-2" footer={forecasts.data ? `${forecasts.data.scored_days} scored day(s) · live forecast MAE ${forecasts.data.live_forecast_mae_kwh ?? "—"} kWh. ${forecasts.data.note}` : null}>
          {forecasts.loading && !forecasts.data ? <ChartSkeleton /> : forecasts.error ? <ErrorState message={forecasts.error} onRetry={forecasts.refetch} /> : forecasts.data?.items.length ? <ForecastChart items={forecasts.data.items} /> : <EmptyState title="No forecasts yet" />}
        </ChartCard>
      </div>

      <div className="mt-4">
        <ChartCard title="Backtest: measured vs weather-adjusted expectation" description="Expectation uses observed weather, so this measures the model, not the weather forecast" footer={backtest.data ? `Algorithm: ${ALGO_LABEL[backtest.data.algorithm] ?? backtest.data.algorithm}` : null}>
          {daily.loading && !daily.data ? <ChartSkeleton /> : daily.data?.items.length ? <DailyGenerationChart items={daily.data.items} expected={expectedMap} /> : <EmptyState title="No data" />}
        </ChartCard>
      </div>

      {scored.length > 0 && (
        <Card className="mt-4">
          <CardHeader><CardTitle className="text-base">Forecast history</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader><TableRow><TableHead>Target</TableHead><TableHead>Issued</TableHead><TableHead className="text-right">Forecast</TableHead><TableHead className="text-right">Actual</TableHead><TableHead className="text-right">Error</TableHead></TableRow></TableHeader>
              <TableBody>
                {[...scored].sort((a, b) => b.target_date.localeCompare(a.target_date)).map((f) => (
                  <TableRow key={`${f.target_date}-${f.issued_at}`}>
                    <TableCell>{shortDate(f.target_date)}</TableCell>
                    <TableCell className="text-muted-foreground">{shortDate(f.issued_at)} (+{f.horizon_days}d)</TableCell>
                    <TableCell className="text-right tabular-nums">{kwh(f.predicted_kwh)}</TableCell>
                    <TableCell className="text-right tabular-nums">{kwh(f.actual_kwh)}</TableCell>
                    <TableCell className="text-right tabular-nums">{f.error_kwh != null ? `${f.error_kwh > 0 ? "+" : ""}${f.error_kwh.toFixed(2)}` : "—"}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
