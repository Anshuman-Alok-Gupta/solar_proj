"use client";

import { AlertTriangle, Database, Loader2, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SOURCE_LABEL } from "@/lib/format";
import { cn } from "@/lib/utils";

export function LoadingBlock({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn("space-y-3", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} className="h-5 w-full" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ChartSkeleton({ height = 260 }: { height?: number }) {
  return (
    <div className="flex items-end gap-1" style={{ height }} aria-busy="true">
      {Array.from({ length: 24 }).map((_, i) => (
        <Skeleton key={i} className="flex-1" style={{ height: `${30 + ((i * 37) % 60)}%` }} />
      ))}
    </div>
  );
}

export function InlineSpinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" /> {label ?? "Loading…"}
    </span>
  );
}

export function ErrorState({ message, onRetry, className }: { message: string; onRetry?: () => void; className?: string }) {
  return (
    <div role="alert" className={cn("flex flex-col items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm", className)}>
      <div className="flex items-center gap-2 font-medium text-destructive">
        <AlertTriangle className="h-4 w-4" /> Something went wrong
      </div>
      <p className="text-muted-foreground break-words">{message}</p>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" /> Retry
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ title, description, action, icon, className }: { title: string; description?: string; action?: React.ReactNode; icon?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-8 text-center", className)}>
      <div className="text-muted-foreground">{icon ?? <Database className="h-8 w-8" />}</div>
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function SourceBadge({ source, className }: { source: string | null | undefined; className?: string }) {
  if (!source) return null;
  const label = SOURCE_LABEL[source] ?? source;
  const variant = source === "synthetic" ? "destructive" : source === "sems_api" ? "default" : "secondary";
  return (
    <Badge variant={variant} className={cn("font-normal", className)}>
      {label}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string }) {
  const cls: Record<string, string> = {
    high: "bg-red-600 text-white",
    medium: "bg-amber-500 text-black",
    low: "bg-sky-600 text-white",
    info: "bg-muted text-foreground",
  };
  return <Badge className={cn("capitalize", cls[severity] ?? "")}>{severity}</Badge>;
}

export function StatusDot({ ok, label }: { ok: boolean | null; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
      <span className={cn("h-2 w-2 rounded-full", ok === null ? "bg-muted-foreground/40" : ok ? "bg-emerald-500" : "bg-red-500")} />
      {label}
    </span>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
