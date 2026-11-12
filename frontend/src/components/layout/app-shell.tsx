"use client";

import { Activity, BarChart3, Bot, Database, FlaskConical, LayoutDashboard, LogOut, Menu, Settings, ShieldAlert, Sun, TrendingUp } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { StatusDot } from "@/components/state";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useApp } from "@/lib/app-context";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/analytics", label: "Energy Analytics", icon: BarChart3 },
  { href: "/forecasting", label: "Forecasting", icon: TrendingUp },
  { href: "/anomalies", label: "Anomaly Centre", icon: ShieldAlert },
  { href: "/analyst", label: "AI Energy Analyst", icon: Bot },
  { href: "/model-lab", label: "Model Lab", icon: FlaskConical },
  { href: "/data", label: "Data Management", icon: Database },
  { href: "/settings", label: "Settings", icon: Settings },
];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <nav className="flex flex-col gap-1" aria-label="Main">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
              active ? "bg-sidebar-accent font-medium text-sidebar-accent-foreground" : "text-sidebar-foreground/80 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground",
            )}
          >
            <Icon className="h-4 w-4 shrink-0" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}

function Brand() {
  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <span className="flex h-8 w-8 items-center justify-center rounded-md bg-amber-500 text-white">
        <Sun className="h-5 w-5" />
      </span>
      <div className="leading-tight">
        <div className="text-sm font-semibold">Solar Intelligence</div>
        <div className="text-[11px] text-muted-foreground">Household energy analytics</div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready, user, plants, plant, selectPlant, logout, capabilities } = useApp();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  if (!ready || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground" aria-busy="true">
        <Activity className="mr-2 h-4 w-4 animate-pulse" /> Loading workspace…
      </div>
    );
  }

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-sidebar p-3 md:flex">
        <Brand />
        <div className="mt-4 flex-1">
          <NavLinks />
        </div>
        <div className="space-y-2 border-t pt-3 text-xs text-muted-foreground">
          <StatusDot ok={capabilities?.sems_configured ?? null} label={capabilities?.sems_configured ? "SEMS connected" : "SEMS not configured"} />
          <StatusDot ok={capabilities?.llm_configured ?? null} label={capabilities?.llm_configured ? `AI: ${capabilities.llm_model}` : "AI analyst not configured"} />
          <div className="truncate pt-1">{user.email}</div>
          <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => { logout(); router.replace("/login"); }}>
            <LogOut className="h-4 w-4" /> Sign out
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur">
          <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger
              render={
                <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation">
                  <Menu className="h-5 w-5" />
                </Button>
              }
            />
            <SheetContent side="left" className="w-64 p-3">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <Brand />
              <div className="mt-4">
                <NavLinks onNavigate={() => setOpen(false)} />
              </div>
            </SheetContent>
          </Sheet>
          <div className="min-w-0 flex-1" />
          {plants.length > 0 && (
            <Select value={plant ? String(plant.id) : undefined} onValueChange={(v) => v && selectPlant(Number(v))}>
              <SelectTrigger className="w-[220px]" aria-label="Select plant">
                <SelectValue placeholder="Select plant" />
              </SelectTrigger>
              <SelectContent>
                {plants.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.name}
                    {p.is_demo ? " (demo)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </header>
        <main className="flex-1 px-4 py-6 md:px-8">
          <div className="mx-auto w-full max-w-7xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
