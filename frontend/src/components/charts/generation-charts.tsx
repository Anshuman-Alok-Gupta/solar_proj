"use client";

import { Bar, BarChart, CartesianGrid, ComposedChart, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, Area, AreaChart } from "recharts";

import { AXIS, GRID_PROPS, Legend, VIZ, VizTooltip } from "./common";
import type { DailyItem, ForecastItem, PredRow } from "@/lib/api";
import { shortDate } from "@/lib/format";

const fmtKwh = (v: number | string | undefined) => (typeof v === "number" ? `${v.toFixed(1)} kWh` : "—");

/** Daily generation bars, optionally with the model's weather-adjusted expectation as a line (same unit). */
export function DailyGenerationChart({ items, expected, height = 280 }: { items: DailyItem[]; expected?: Record<string, number>; height?: number }) {
  const data = items.map((d) => ({ date: d.date, kwh: d.generation_kwh, expected: expected?.[d.date] ?? null, partial: !d.is_complete, source: d.source }));
  const hasExpected = !!expected && Object.keys(expected).length > 0;
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }} barCategoryGap="20%">
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} unit="" width={48} />
          <Tooltip cursor={{ fill: "var(--muted)" }} content={<VizTooltip formatter={fmtKwh} labelFormatter={(l) => shortDate(String(l))} />} />
          <Bar dataKey="kwh" name="Generation" fill={VIZ.generation} radius={[4, 4, 0, 0]} maxBarSize={28} isAnimationActive={false} />
          {hasExpected && <Line dataKey="expected" name="Expected (weather-adjusted)" stroke={VIZ.forecast} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />}
        </ComposedChart>
      </ResponsiveContainer>
      <Legend items={[{ label: "Measured generation", color: VIZ.generation }, ...(hasExpected ? [{ label: "Model expectation (observed weather)", color: VIZ.forecast }] : [])]} />
    </div>
  );
}

export function IrradianceChart({ items, height = 160 }: { items: DailyItem[]; height?: number }) {
  const data = items.map((d) => ({ date: d.date, gti: d.gti_wh != null ? Math.round(d.gti_wh) : null, cloud: d.cloud_cover }));
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48} />
          <Tooltip content={<VizTooltip formatter={(v, n) => (typeof v === "number" ? (n.includes("Cloud") ? `${v.toFixed(0)}%` : `${v} Wh/m²`) : "—")} labelFormatter={(l) => shortDate(String(l))} />} />
          <Area dataKey="gti" name="Tilted irradiance" stroke={VIZ.weather} fill={VIZ.weather} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} connectNulls />
        </AreaChart>
      </ResponsiveContainer>
      <Legend items={[{ label: "Tilted-plane irradiance (Wh/m²/day, Open-Meteo)", color: VIZ.weather }]} />
    </div>
  );
}

export function IntradayChart({ readings, weather, capacityKw, height = 260 }: { readings: { local_time: string; power_w: number | null; source: string }[]; weather: { local_time: string; gti: number | null }[]; capacityKw?: number | null; height?: number }) {
  const data = readings.map((r) => ({ t: r.local_time, w: r.power_w }));
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <AreaChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="t" tick={AXIS} axisLine={false} tickLine={false} minTickGap={40} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={56} tickFormatter={(v) => `${(Number(v) / 1000).toFixed(1)}k`} />
          <Tooltip content={<VizTooltip formatter={(v) => (typeof v === "number" ? `${v.toFixed(0)} W` : "—")} />} />
          {capacityKw ? <ReferenceLine y={capacityKw * 1000} stroke={VIZ.text} strokeDasharray="4 4" label={{ value: `${capacityKw} kW`, position: "insideTopRight", fontSize: 10, fill: VIZ.text }} /> : null}
          <Area dataKey="w" name="AC power" stroke={VIZ.generation} fill={VIZ.generation} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} connectNulls />
        </AreaChart>
      </ResponsiveContainer>
      {weather.length > 0 && (
        <ResponsiveContainer width="100%" height={90}>
          <AreaChart data={weather.map((w) => ({ t: w.local_time, gti: w.gti }))} margin={{ top: 4, right: 8, left: -8, bottom: 0 }}>
            <XAxis dataKey="t" tick={AXIS} axisLine={false} tickLine={false} minTickGap={40} hide />
            <YAxis tick={AXIS} axisLine={false} tickLine={false} width={56} />
            <Tooltip content={<VizTooltip formatter={(v) => (typeof v === "number" ? `${v.toFixed(0)} W/m²` : "—")} />} />
            <Area dataKey="gti" name="Tilted irradiance" stroke={VIZ.weather} fill={VIZ.weather} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      )}
      <Legend items={[{ label: "AC power (W)", color: VIZ.generation }, { label: "Hourly tilted irradiance (W/m²)", color: VIZ.weather }]} />
    </div>
  );
}

export function MonthlyChart({ items, height = 240 }: { items: Record<string, number | string>[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={items} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis dataKey="month" tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48} />
        <Tooltip cursor={{ fill: "var(--muted)" }} content={<VizTooltip formatter={fmtKwh} />} />
        <Bar dataKey="total_kwh" name="Monthly generation" fill={VIZ.generation} radius={[4, 4, 0, 0]} maxBarSize={48} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function WeeklyChart({ items, height = 220 }: { items: Record<string, number | string>[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={items} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis dataKey="week_start" tickFormatter={(v) => shortDate(String(v))} tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48} />
        <Tooltip cursor={{ fill: "var(--muted)" }} content={<VizTooltip formatter={fmtKwh} labelFormatter={(l) => `Week of ${shortDate(String(l))}`} />} />
        <Bar dataKey="total_kwh" name="Weekly generation" fill={VIZ.generation} radius={[4, 4, 0, 0]} maxBarSize={40} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DistributionChart({ bins, height = 220 }: { bins: { from_kwh: number; to_kwh: number; days: number }[]; height?: number }) {
  const data = bins.map((b) => ({ label: `${b.from_kwh.toFixed(0)}–${b.to_kwh.toFixed(0)}`, days: b.days }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis dataKey="label" tick={AXIS} axisLine={false} tickLine={false} interval={0} angle={-30} textAnchor="end" height={44} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} allowDecimals={false} width={40} />
        <Tooltip cursor={{ fill: "var(--muted)" }} content={<VizTooltip formatter={(v) => `${v} days`} labelFormatter={(l) => `${l} kWh`} />} />
        <Bar dataKey="days" name="Days" fill={VIZ.generation} radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function WeatherScatter({ items, height = 260 }: { items: DailyItem[]; height?: number }) {
  const data = items.filter((d) => d.gti_wh != null).map((d) => ({ x: Math.round(d.gti_wh!), y: d.generation_kwh, date: d.date, cloud: d.cloud_cover }));
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ScatterChart margin={{ top: 8, right: 8, left: -8, bottom: 8 }}>
          <CartesianGrid {...GRID_PROPS} vertical />
          <XAxis dataKey="x" type="number" name="Irradiance" tick={AXIS} axisLine={false} tickLine={false} label={{ value: "Tilted irradiance (Wh/m²/day)", position: "insideBottom", offset: -4, fontSize: 11, fill: VIZ.text }} />
          <YAxis dataKey="y" type="number" name="Generation" tick={AXIS} axisLine={false} tickLine={false} width={48} />
          <Tooltip cursor={{ strokeDasharray: "3 3" }} content={<VizTooltip formatter={(v, n) => (typeof v === "number" ? (n === "Generation" ? `${v.toFixed(1)} kWh` : `${v} Wh/m²`) : "—")} labelFormatter={() => ""} />} />
          <Scatter data={data} fill={VIZ.generation} fillOpacity={0.75} isAnimationActive={false} />
        </ScatterChart>
      </ResponsiveContainer>
      <Legend items={[{ label: "One point per day", color: VIZ.generation }]} />
    </div>
  );
}

export function ForecastChart({ items, height = 280 }: { items: ForecastItem[]; height?: number }) {
  const data = [...items].sort((a, b) => a.target_date.localeCompare(b.target_date)).map((f) => ({ date: f.target_date, predicted: f.predicted_kwh, actual: f.actual_kwh, lower: f.lower_kwh, upper: f.upper_kwh, band: f.lower_kwh != null && f.upper_kwh != null ? [f.lower_kwh, f.upper_kwh] : null }));
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48} />
          <Tooltip content={<VizTooltip formatter={(v, n) => (Array.isArray(v) ? `${(v as number[])[0]?.toFixed(1)}–${(v as number[])[1]?.toFixed(1)} kWh` : fmtKwh(v)) + (n ? "" : "")} labelFormatter={(l) => shortDate(String(l))} />} />
          <Area dataKey="band" name="10–90% interval" stroke="none" fill={VIZ.forecast} fillOpacity={0.15} isAnimationActive={false} connectNulls />
          <Line dataKey="predicted" name="Forecast" stroke={VIZ.forecast} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
          <Line dataKey="actual" name="Actual" stroke={VIZ.generation} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
      <Legend items={[{ label: "Actual (measured)", color: VIZ.generation }, { label: "Forecast (forecast weather)", color: VIZ.forecast }]} />
    </div>
  );
}

export function ActualVsPredictedChart({ rows, height = 260 }: { rows: PredRow[]; height?: number }) {
  const data = [...rows].sort((a, b) => a.date.localeCompare(b.date));
  return (
    <div>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
          <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48} />
          <Tooltip content={<VizTooltip formatter={fmtKwh} labelFormatter={(l) => shortDate(String(l))} />} />
          <Line dataKey="actual" name="Actual" stroke={VIZ.generation} strokeWidth={2} dot={{ r: 2.5 }} isAnimationActive={false} />
          <Line dataKey="predicted" name="Predicted" stroke={VIZ.forecast} strokeWidth={2} dot={{ r: 2.5 }} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
      <Legend items={[{ label: "Actual", color: VIZ.generation }, { label: "Predicted (out-of-sample)", color: VIZ.forecast }]} />
    </div>
  );
}

export function ResidualChart({ rows, height = 200 }: { rows: PredRow[]; height?: number }) {
  const data = [...rows].sort((a, b) => a.date.localeCompare(b.date)).map((r) => ({ date: r.date, residual: +(r.actual - r.predicted).toFixed(2) }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} />
        <XAxis dataKey="date" tickFormatter={shortDate} tick={AXIS} axisLine={false} tickLine={false} minTickGap={24} />
        <YAxis tick={AXIS} axisLine={false} tickLine={false} width={48} />
        <ReferenceLine y={0} stroke={VIZ.text} />
        <Tooltip cursor={{ fill: "var(--muted)" }} content={<VizTooltip formatter={(v) => (typeof v === "number" ? `${v > 0 ? "+" : ""}${v.toFixed(2)} kWh` : "—")} labelFormatter={(l) => shortDate(String(l))} />} />
        <Bar dataKey="residual" name="Actual − predicted" fill={VIZ.generation} radius={[3, 3, 3, 3]} maxBarSize={20} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ImportanceChart({ items, height }: { items: { label: string; value: number }[]; height?: number }) {
  const data = [...items].sort((a, b) => b.value - a.value).slice(0, 12);
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(160, data.length * 26)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, left: 8, bottom: 0 }}>
        <CartesianGrid {...GRID_PROPS} horizontal={false} vertical />
        <XAxis type="number" tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="label" tick={AXIS} axisLine={false} tickLine={false} width={190} />
        <Tooltip cursor={{ fill: "var(--muted)" }} content={<VizTooltip formatter={(v) => (typeof v === "number" ? v.toFixed(3) : "—")} />} />
        <Bar dataKey="value" name="Importance" fill={VIZ.generation} radius={[0, 4, 4, 0]} maxBarSize={16} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
