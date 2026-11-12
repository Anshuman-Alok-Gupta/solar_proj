"use client";

import { IndianRupee } from "lucide-react";
import Link from "next/link";

import { ErrorState, LoadingBlock } from "@/components/state";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { endpoints } from "@/lib/api";
import { kwh, money, num } from "@/lib/format";
import { useApi } from "@/lib/use-api";

type SavingsData = {
  currency?: string;
  generation_kwh?: number;
  estimated_value?: number | null;
  estimated_value_if_all_exported?: number | null;
  estimated_value_if_all_self_consumed?: number | null;
  effective_value_per_kwh?: number | null;
  offset_kwh?: number | null;
  surplus_kwh?: number | null;
  bill_without_solar_per_month?: number | null;
  monthly_consumption_kwh?: number | null;
  missing_inputs?: string[];
  period?: { start: string | null; days: number };
};

/** Estimated value of the generation to date (net metering; estimate, not a bill). */
export function SavingsCard({ plantId }: { plantId: number }) {
  const q = useApi(() => endpoints.savings(plantId) as Promise<SavingsData>, [plantId]);
  const d = q.data;
  const cur = d?.currency ?? "INR";
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <IndianRupee className="h-4 w-4" /> Savings so far <span className="text-xs font-normal text-muted-foreground">(estimate)</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {q.loading && !d ? (
          <LoadingBlock lines={3} />
        ) : q.error ? (
          <ErrorState message={q.error} onRetry={q.refetch} />
        ) : d && d.estimated_value != null ? (
          <>
            <div className="text-2xl font-semibold tabular-nums">{money(d.estimated_value, cur)}</div>
            <p className="text-muted-foreground">
              from {kwh(d.generation_kwh ?? null, 0)} over {d.period?.days ?? 0} days
              {d.effective_value_per_kwh != null && <> · about {cur === "INR" ? "₹" : `${cur} `}{num(d.effective_value_per_kwh, 2)} per unit</>}
            </p>
            {d.offset_kwh != null && (
              <dl className="grid grid-cols-2 gap-y-0.5 text-xs">
                <dt className="text-muted-foreground">Units off your bill</dt><dd className="tabular-nums">{kwh(d.offset_kwh, 0)}</dd>
                <dt className="text-muted-foreground">Surplus (paid at year end)</dt><dd className="tabular-nums">{kwh(d.surplus_kwh ?? null, 0)}</dd>
                {d.bill_without_solar_per_month != null && (<><dt className="text-muted-foreground">Bill without solar</dt><dd className="tabular-nums">{money(d.bill_without_solar_per_month, cur)}/month</dd></>)}
              </dl>
            )}
            <p className="text-xs text-muted-foreground">Assumes {num(d.monthly_consumption_kwh ?? null, 0)} units/month household use; energy charges only. <Link href="/settings" className="underline">Adjust</Link></p>
          </>
        ) : d ? (
          <>
            {d.estimated_value_if_all_exported != null && d.estimated_value_if_all_self_consumed != null && (
              <div className="text-lg font-semibold tabular-nums">
                {money(d.estimated_value_if_all_exported, cur)} – {money(d.estimated_value_if_all_self_consumed, cur)}
              </div>
            )}
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
              {(d.missing_inputs ?? []).map((m) => <li key={m}>{m}</li>)}
            </ul>
            <Link href="/settings" className="text-xs underline">Set it in Settings</Link>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
