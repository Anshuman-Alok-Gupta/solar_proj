"use client";

import type { TooltipContentProps } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export const VIZ = {
  generation: "var(--viz-1)",
  weather: "var(--viz-2)",
  forecast: "var(--viz-3)",
  secondary: "var(--viz-4)",
  good: "var(--viz-good)",
  warning: "var(--viz-warning)",
  serious: "var(--viz-serious)",
  critical: "var(--viz-critical)",
  grid: "var(--viz-grid)",
  text: "var(--viz-text)",
};

export const AXIS = { fontSize: 11, fill: VIZ.text } as const;
export const GRID_PROPS = { stroke: VIZ.grid, strokeDasharray: "2 4", vertical: false } as const;

export function ChartCard({ title, description, children, className, action, footer }: { title: string; description?: string; children: React.ReactNode; className?: string; action?: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <Card className={cn("flex flex-col", className)}>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="text-base">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {action}
      </CardHeader>
      <CardContent className="flex-1">{children}</CardContent>
      {footer && <div className="border-t px-6 py-3 text-xs text-muted-foreground">{footer}</div>}
    </Card>
  );
}

type Row = { name?: string | number; value?: number | string; color?: string; dataKey?: string | number; payload?: Record<string, unknown>; unit?: string };

/** Compact tooltip: label line + one row per series, text in ink colours with a colour chip for identity. */
export function VizTooltip({ active, payload, label, formatter, labelFormatter }: Partial<Pick<TooltipContentProps<number | string, string>, "active" | "payload" | "label">> & { formatter?: (v: number | string | undefined, name: string, row: Row) => string; labelFormatter?: (l: unknown) => string }) {
  if (!active || !payload || payload.length === 0) return null;
  const rows = payload as unknown as Row[];
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <div className="mb-1 font-medium text-foreground">{labelFormatter ? labelFormatter(label) : String(label ?? "")}</div>
      <div className="space-y-0.5">
        {rows.map((p, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="h-2 w-2 rounded-full" style={{ background: p.color }} />
            <span className="text-muted-foreground">{String(p.name ?? p.dataKey)}</span>
            <span className="ml-auto font-medium tabular-nums text-foreground">{formatter ? formatter(p.value, String(p.name ?? p.dataKey), p) : String(p.value ?? "—")}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; dashed?: boolean }[] }) {
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-label="Legend">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: it.color, borderTop: it.dashed ? `2px dashed ${it.color}` : undefined, height: it.dashed ? 0 : undefined }} />
          {it.label}
        </span>
      ))}
    </div>
  );
}
