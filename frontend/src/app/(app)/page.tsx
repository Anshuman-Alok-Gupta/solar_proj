"use client";

import { Activity, ArrowRight, CloudSun, Gauge, ShieldAlert, Sun, TrendingUp, Zap } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { ChartCard } from "@/components/charts/common";
import { DailyGenerationChart } from "@/components/charts/generation-charts";
import { SavingsCard } from "@/components/savings-card";
import { ChartSkeleton, EmptyState, ErrorState, LoadingBlock, PageHeader, SeverityBadge, SourceBadge, StatusDot } from "@/components/state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { CATEGORY_LABEL, ago, kwh, longDate, num, shortDate, watts } from "@/lib/format";
import { useApi } from "@/lib/use-api";

function Stat({ label, value, hint, icon: Icon }: { label: string; value: string; hint?: React.ReactNode; icon: React.ComponentType<{ className?: string }> }) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          {label}
          <Icon className="h-4 w-4" />
        </div>
        <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
        {hint && <div className="mt-1 text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  );
}

export default function OverviewPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const summary = useApi(pid ? () => endpoints.summary(pid) : null, [pid]);
  const daily = useApi(pid ? () => endpoints.daily(pid, { start: isoDaysAgo(44) }) : null, [pid]);
  const anomalies = useApi(pid ? () => endpoints.anomalies(pid, { status: "open", limit: 5 }) : null, [pid]);
  const forecastWx = useApi(pid ? () => endpoints.weatherDaily(pid, { kind: "forecast", start: isoDaysAgo(0), end: isoDaysAgo(-5) }) : null, [pid]);
  const sems = useApi(pid ? () => endpoints.semsStatus(pid) : null, [pid]);

  const s = summary.data;
  const todayWx = useMemo(() => forecastWx.data?.items?.[0] ?? null, [forecastWx.data]);

  if (!plant) {
    return <EmptyState title="No plant configured" description="Create your plant in Settings to start collecting data." action={<Button render={<Link href="/settings" />}>Go to Settings</Button>} />;
  }

  return (
    <div>
      <PageHeader
        title={plant.name}
        description={`${plant.capacity_kw ?? "?"} kW · ${plant.inverter_manufacturer ?? ""} ${plant.inverter_model ?? ""} · ${plant.timezone}`}
        actions={
          <>
            {plant.is_demo && <Badge variant="destructive">Demo plant</Badge>}
            <Button variant="outline" size="sm" render={<Link href="/analyst" />}>
              Ask the analyst <ArrowRight className="h-4 w-4" />
            </Button>
          </>
        }
      />

      {summary.error && <ErrorState message={summary.error} onRetry={summary.refetch} className="mb-6" />}

      {summary.loading && !s ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i}><CardContent className="p-4"><LoadingBlock lines={2} /></CardContent></Card>
          ))}
        </div>
      ) : s && !s.has_data ? (
        <EmptyState title="No generation data yet" description="Connect SEMS or import a CSV/Excel export on the Data Management page. Nothing is shown until real data exists." action={<Button render={<Link href="/data" />}>Open Data Management</Button>} />
      ) : s ? (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label="Latest power" value={s.latest_reading ? watts(s.latest_reading.power_w) : "—"} icon={Zap} hint={s.latest_reading ? <span className="inline-flex items-center gap-2"><StatusDot ok={s.latest_reading.is_live} label={s.latest_reading.is_live ? "live" : `last reading ${ago(s.latest_reading.ts)}`} /></span> : "No live readings yet"} />
            <Stat label={`Today (${shortDate(s.today)})`} value={kwh(s.today_kwh)} icon={Sun} hint={s.today_is_partial ? "Partial day, still accumulating" : "Complete day"} />
            <Stat label="This month" value={kwh(s.this_month_kwh)} icon={Gauge} hint={s.last_month_kwh != null ? `Last month ${kwh(s.last_month_kwh)}` : undefined} />
            <Stat label="Lifetime" value={kwh(s.lifetime_kwh)} icon={Activity} hint={s.data_range ? `${s.data_range.days} days since ${shortDate(s.data_range.first)}` : undefined} />
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-3">
            <ChartCard title="Daily generation" description="Last 45 days, measured by the inverter" className="lg:col-span-2" footer={daily.data ? <span>Total {kwh(daily.data.total_kwh)} over {daily.data.count} days · sources: {Array.from(new Set(daily.data.items.map((i) => i.source))).map((src) => <SourceBadge key={src} source={src} className="ml-1" />)}</span> : null}>
              {daily.loading && !daily.data ? <ChartSkeleton /> : daily.error ? <ErrorState message={daily.error} onRetry={daily.refetch} /> : daily.data && daily.data.items.length ? <DailyGenerationChart items={daily.data.items} /> : <EmptyState title="No daily data in this window" />}
            </ChartCard>

            <div className="flex flex-col gap-4">
              <Card>
                <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><TrendingUp className="h-4 w-4" /> Tomorrow&apos;s forecast</CardTitle></CardHeader>
                <CardContent>
                  {s.next_day_forecast ? (
                    <div>
                      <div className="text-3xl font-semibold tabular-nums">{kwh(s.next_day_forecast.predicted_kwh)}</div>
                      <div className="text-xs text-muted-foreground">
                        {longDate(s.next_day_forecast.target_date)}
                        {s.next_day_forecast.lower_kwh != null && s.next_day_forecast.upper_kwh != null && <> · interval {num(s.next_day_forecast.lower_kwh)}–{num(s.next_day_forecast.upper_kwh)} kWh</>}
                      </div>
                      <div className="mt-2 text-xs text-muted-foreground">Estimate from {s.next_day_forecast.weather_input_kind} weather · issued {ago(s.next_day_forecast.issued_at)}</div>
                    </div>
                  ) : (
                    <EmptyState title="No forecast yet" description="Train a model and issue a forecast on the Forecasting page." className="p-4" action={<Button size="sm" variant="outline" render={<Link href="/forecasting" />}>Forecasting</Button>} />
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><CloudSun className="h-4 w-4" /> Weather today</CardTitle></CardHeader>
                <CardContent className="text-sm">
                  {forecastWx.loading && !forecastWx.data ? <LoadingBlock lines={2} /> : todayWx ? (
                    <dl className="grid grid-cols-2 gap-y-1 text-sm">
                      <dt className="text-muted-foreground">Tilted irradiance</dt><dd className="tabular-nums">{num(todayWx.gti_sum_wh_m2 as number, 0)} Wh/m²</dd>
                      <dt className="text-muted-foreground">Sunshine</dt><dd className="tabular-nums">{num(todayWx.sunshine_hours as number, 1)} h</dd>
                      <dt className="text-muted-foreground">Cloud cover</dt><dd className="tabular-nums">{num(todayWx.cloud_cover_mean as number, 0)}%</dd>
                      <dt className="text-muted-foreground">Max temp</dt><dd className="tabular-nums">{num(todayWx.temperature_max as number, 1)} °C</dd>
                    </dl>
                  ) : (
                    <p className="text-muted-foreground">No weather forecast stored. Set the plant location and run a weather sync.</p>
                  )}
                  <p className="mt-2 text-xs text-muted-foreground">Open-Meteo forecast for the plant location</p>
                </CardContent>
              </Card>
            </div>
          </div>

          <div className="mt-6 grid gap-4 lg:grid-cols-3">
            <SavingsCard plantId={plant.id} />
            <Card>
              <CardHeader className="pb-2"><CardTitle className="flex items-center gap-2 text-base"><ShieldAlert className="h-4 w-4" /> Open alerts {s.open_anomalies > 0 && <Badge variant="secondary">{s.open_anomalies}</Badge>}</CardTitle></CardHeader>
              <CardContent>
                {anomalies.loading && !anomalies.data ? <LoadingBlock /> : anomalies.error ? <ErrorState message={anomalies.error} onRetry={anomalies.refetch} /> : anomalies.data && anomalies.data.items.length ? (
                  <ul className="divide-y">
                    {anomalies.data.items.map((a) => (
                      <li key={a.id} className="flex items-start gap-3 py-2 text-sm">
                        <SeverityBadge severity={a.severity} />
                        <div className="min-w-0 flex-1">
                          <div className="truncate font-medium">{CATEGORY_LABEL[a.category] ?? a.category}</div>
                          <div className="truncate text-xs text-muted-foreground">{shortDate(a.date)} · {a.title}</div>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : <p className="text-sm text-muted-foreground">No open anomalies. Run detection on the Anomaly Centre page after new data arrives.</p>}
                <Button variant="link" className="mt-1 px-0" render={<Link href="/anomalies" />}>Open Anomaly Centre <ArrowRight className="h-4 w-4" /></Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2"><CardTitle className="text-base">Data status</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-sm">
                <div className="flex items-center justify-between"><span className="text-muted-foreground">Last successful SEMS sync</span><span>{s.last_successful_sems_sync ? ago(s.last_successful_sems_sync) : "never"}</span></div>
                <div className="flex items-center justify-between"><span className="text-muted-foreground">Latest reading</span><span>{s.latest_reading ? `${ago(s.latest_reading.ts)} (${s.latest_reading.source})` : "—"}</span></div>
                <div className="flex items-center justify-between"><span className="text-muted-foreground">Daily history</span><span>{s.data_range ? `${shortDate(s.data_range.first)} → ${shortDate(s.data_range.last)} (${s.data_range.days} days)` : "—"}</span></div>
                <div className="flex items-center justify-between"><span className="text-muted-foreground">Best day</span><span>{s.best_day ? `${kwh(s.best_day.kwh)} on ${shortDate(s.best_day.date)}` : "—"}</span></div>
                <div className="flex items-center justify-between"><span className="text-muted-foreground">30-day specific yield</span><span>{s.specific_yield_30d_kwh_per_kw != null ? `${s.specific_yield_30d_kwh_per_kw} kWh/kW/day` : "—"}</span></div>
                {sems.data && (sems.data as { has_smart_meter?: boolean | null }).has_smart_meter === false && <p className="text-xs text-muted-foreground">No smart meter detected: consumption and export are not measured.</p>}
                <Button variant="link" className="px-0" render={<Link href="/data" />}>Data Management <ArrowRight className="h-4 w-4" /></Button>
              </CardContent>
            </Card>
          </div>
        </>
      ) : null}
    </div>
  );
}

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}
