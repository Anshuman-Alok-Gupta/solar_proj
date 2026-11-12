"use client";

import { useState } from "react";

import { ChartCard } from "@/components/charts/common";
import { DailyGenerationChart, DistributionChart, IntradayChart, IrradianceChart, MonthlyChart, WeatherScatter, WeeklyChart } from "@/components/charts/generation-charts";
import { ChartSkeleton, EmptyState, ErrorState, LoadingBlock, PageHeader, SourceBadge } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { kwh, num, pct, shortDate } from "@/lib/format";
import { useApi } from "@/lib/use-api";

const RANGES = [
  { label: "30 days", days: 30 },
  { label: "90 days", days: 90 },
  { label: "1 year", days: 365 },
];

function iso(d: Date) {
  return d.toISOString().slice(0, 10);
}

export default function AnalyticsPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const [days, setDays] = useState(90);
  const [now] = useState(() => Date.now());
  const [day, setDay] = useState(() => iso(new Date(now)));
  const start = iso(new Date(now - days * 86400000));

  const daily = useApi(pid ? () => endpoints.daily(pid, { start }) : null, [pid, start]);
  const monthly = useApi(pid ? () => endpoints.monthly(pid) : null, [pid]);
  const weekly = useApi(pid ? () => endpoints.weekly(pid, 26) : null, [pid]);
  const dist = useApi(pid ? () => endpoints.distribution(pid, { start }) : null, [pid, start]);
  const corr = useApi(pid ? () => endpoints.weatherCorrelation(pid, { start }) : null, [pid, start]);
  const compare = useApi(pid ? () => endpoints.compare(pid) : null, [pid]);
  const intraday = useApi(pid ? () => endpoints.intraday(pid, day) : null, [pid, day]);

  if (!plant) return <EmptyState title="No plant selected" />;

  return (
    <div>
      <PageHeader
        title="Energy Analytics"
        description="Daily, weekly and monthly generation, distribution and weather relationships."
        actions={RANGES.map((r) => (
          <Button key={r.days} size="sm" variant={days === r.days ? "default" : "outline"} onClick={() => setDays(r.days)}>
            {r.label}
          </Button>
        ))}
      />

      <Tabs defaultValue="trend">
        <TabsList className="mb-4">
          <TabsTrigger value="trend">Trend</TabsTrigger>
          <TabsTrigger value="periods">Periods</TabsTrigger>
          <TabsTrigger value="distribution">Distribution</TabsTrigger>
          <TabsTrigger value="weather">Weather vs generation</TabsTrigger>
          <TabsTrigger value="intraday">Intraday</TabsTrigger>
        </TabsList>

        <TabsContent value="trend" className="space-y-4">
          <ChartCard title="Daily generation" description={`From ${shortDate(start)} · ${daily.data?.count ?? 0} days · ${kwh(daily.data?.total_kwh)}`}>
            {daily.loading && !daily.data ? <ChartSkeleton /> : daily.error ? <ErrorState message={daily.error} onRetry={daily.refetch} /> : daily.data?.items.length ? <DailyGenerationChart items={daily.data.items} /> : <EmptyState title="No data in this range" />}
          </ChartCard>
          <ChartCard title="Daily tilted-plane irradiance" description="Observed weather (ERA5 reanalysis / archived forecasts) aligned to the same days">
            {daily.loading && !daily.data ? <ChartSkeleton height={160} /> : daily.data?.items.some((i) => i.gti_wh != null) ? <IrradianceChart items={daily.data.items} /> : <EmptyState title="No weather data" description="Set the plant location and run a weather sync on the Data page." />}
          </ChartCard>
        </TabsContent>

        <TabsContent value="periods" className="space-y-4">
          <div className="grid gap-4 lg:grid-cols-2">
            <ChartCard title="Monthly generation">
              {monthly.loading && !monthly.data ? <ChartSkeleton height={240} /> : monthly.data?.items.length ? <MonthlyChart items={monthly.data.items} /> : <EmptyState title="No monthly data" />}
            </ChartCard>
            <ChartCard title="Weekly generation" description="Last 26 weeks (weeks start Monday)">
              {weekly.loading && !weekly.data ? <ChartSkeleton height={220} /> : weekly.data?.items.length ? <WeeklyChart items={weekly.data.items} /> : <EmptyState title="No weekly data" />}
            </ChartCard>
          </div>
          <Card>
            <CardHeader><CardTitle className="text-base">Period comparison</CardTitle></CardHeader>
            <CardContent>
              {compare.loading && !compare.data ? <LoadingBlock /> : compare.data ? (
                <Table>
                  <TableHeader><TableRow><TableHead>Period</TableHead><TableHead>Dates</TableHead><TableHead className="text-right">Days</TableHead><TableHead className="text-right">Total</TableHead><TableHead className="text-right">Avg / day</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {compare.data.periods.map((p) => (
                      <TableRow key={p.name}>
                        <TableCell className="font-medium">{p.name.replace(/_/g, " ")}</TableCell>
                        <TableCell className="text-muted-foreground">{shortDate(p.start)} – {shortDate(p.end)}</TableCell>
                        <TableCell className="text-right tabular-nums">{p.days_with_data}</TableCell>
                        <TableCell className="text-right tabular-nums">{kwh(p.total_kwh)}</TableCell>
                        <TableCell className="text-right tabular-nums">{kwh(p.avg_daily_kwh)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : null}
            </CardContent>
          </Card>
          {monthly.data?.items.length ? (
            <Card>
              <CardHeader><CardTitle className="text-base">Monthly table</CardTitle></CardHeader>
              <CardContent>
                <Table>
                  <TableHeader><TableRow><TableHead>Month</TableHead><TableHead className="text-right">Total</TableHead><TableHead className="text-right">Avg/day</TableHead><TableHead className="text-right">Best</TableHead><TableHead className="text-right">Lowest</TableHead><TableHead className="text-right">Coverage</TableHead><TableHead className="text-right">kWh/kW</TableHead></TableRow></TableHeader>
                  <TableBody>
                    {monthly.data.items.map((m) => (
                      <TableRow key={String(m.month)}>
                        <TableCell className="font-medium">{m.month}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(m.total_kwh as number)}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(m.avg_daily_kwh as number, 2)}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(m.best_day_kwh as number)}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(m.worst_day_kwh as number)}</TableCell>
                        <TableCell className="text-right tabular-nums">{pct((m.coverage as number) * 100).replace("+", "")}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(m.specific_yield_kwh_per_kw as number)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          ) : null}
        </TabsContent>

        <TabsContent value="distribution">
          <div className="grid gap-4 lg:grid-cols-3">
            <ChartCard title="Distribution of daily generation" description="Complete days only" className="lg:col-span-2">
              {dist.loading && !dist.data ? <ChartSkeleton height={220} /> : dist.data?.bins.length ? <DistributionChart bins={dist.data.bins} /> : <EmptyState title="Not enough days" description="At least two complete days are needed." />}
            </ChartCard>
            <Card>
              <CardHeader><CardTitle className="text-base">Statistics</CardTitle></CardHeader>
              <CardContent>
                {dist.data?.stats ? (
                  <dl className="grid grid-cols-2 gap-y-1 text-sm">
                    {Object.entries(dist.data.stats).map(([k, v]) => (<><dt key={`${k}-k`} className="text-muted-foreground">{k}</dt><dd key={`${k}-v`} className="tabular-nums">{num(v, 2)} kWh</dd></>))}
                    <dt className="text-muted-foreground">days</dt><dd className="tabular-nums">{dist.data.count}</dd>
                  </dl>
                ) : <p className="text-sm text-muted-foreground">—</p>}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="weather">
          <div className="grid gap-4 lg:grid-cols-3">
            <ChartCard title="Irradiance vs generation" description="Each point is one complete day" className="lg:col-span-2" footer={corr.data?.note}>
              {corr.loading && !corr.data ? <ChartSkeleton /> : corr.data?.items.length ? <WeatherScatter items={corr.data.items} /> : <EmptyState title="No overlapping weather and generation days" />}
            </ChartCard>
            <Card>
              <CardHeader><CardTitle className="text-base">Correlation with daily kWh</CardTitle></CardHeader>
              <CardContent>
                {corr.data ? (
                  <Table>
                    <TableBody>
                      {Object.entries(corr.data.correlations).map(([k, v]) => (
                        <TableRow key={k}><TableCell className="text-muted-foreground">{k}</TableCell><TableCell className="text-right tabular-nums">{v == null ? "—" : v.toFixed(2)}</TableCell></TableRow>
                      ))}
                    </TableBody>
                  </Table>
                ) : <LoadingBlock />}
                <p className="mt-2 text-xs text-muted-foreground">Pearson r over {corr.data?.count ?? 0} days. Association, not causation.</p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="intraday">
          <ChartCard title="Power curve" description="5-minute / live readings for one day" action={<Input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="w-[160px]" aria-label="Day" />} footer={intraday.data?.sources.length ? <span>Sources: {intraday.data.sources.map((s) => <SourceBadge key={s} source={s} className="ml-1" />)}</span> : null}>
            {intraday.loading && !intraday.data ? <ChartSkeleton /> : intraday.error ? <ErrorState message={intraday.error} onRetry={intraday.refetch} /> : intraday.data?.readings.length ? <IntradayChart readings={intraday.data.readings} weather={intraday.data.weather_hourly} capacityKw={plant.capacity_kw} /> : <EmptyState title="No interval readings for this day" description="Only daily totals exist for this date. Import the SEMS 'Inverter Operation Report' for 5-minute data, or enable scheduled SEMS polling for live readings." />}
          </ChartCard>
        </TabsContent>
      </Tabs>
    </div>
  );
}
