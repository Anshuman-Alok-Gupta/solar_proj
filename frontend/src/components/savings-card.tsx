"use client";

import { IndianRupee } from "lucide-react";
import Link from "next/link";

import { ErrorState, LoadingBlock } from "@/components/state";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { endpoints } from "@/lib/api";
import { kwh, money, num, shortDate } from "@/lib/format";
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

/** Savings to date: measured from uploaded bills, estimated only for days no bill covers yet. */
export function SavingsCard({ plantId }: { plantId: number }) {
  const q = useApi(() => endpoints.savings(plantId) as Promise<SavingsData>, [plantId]);
  const bills = useApi(() => endpoints.bills(plantId), [plantId]);
  const toDate = bills.data?.summary.to_date ?? null;
  const d = q.data;
  const cur = d?.currency ?? "INR";
  const label = toDate ? (toDate.estimated ? "(bills + estimate)" : "(from bills)") : "(estimate)";
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <IndianRupee className="h-4 w-4" /> Savings so far <span className="text-xs font-normal text-muted-foreground">{label}</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {(q.loading && !d) || (bills.loading && !bills.data) ? (
          <LoadingBlock lines={3} />
        ) : toDate ? (
          <>
            <div className="text-2xl font-semibold tabular-nums">{money(toDate.total, cur)}</div>
            <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-xs">
              <dt className="text-muted-foreground">
                Measured from {toDate.measured_bills} bill{toDate.measured_bills === 1 ? "" : "s"} ({shortDate(toDate.measured_from)} – {shortDate(toDate.measured_to)})
              </dt>
              <dd className="text-right tabular-nums">{money(toDate.measured, cur)}</dd>
              {toDate.estimated_days > 0 && (
                <>
                  <dt className="text-muted-foreground">Estimated, {toDate.estimated_days} day{toDate.estimated_days === 1 ? "" : "s"} not on a bill yet</dt>
                  <dd className="text-right tabular-nums">{toDate.estimate_missing_tariff && !toDate.estimated ? "—" : money(toDate.estimated, cur)}</dd>
                </>
              )}
            </dl>
            {toDate.estimate_missing_tariff && (
              <p className="text-xs text-muted-foreground">Set a tariff in <Link href="/settings" className="underline">Settings</Link> to estimate the days not billed yet.</p>
            )}
            <p className="text-xs text-muted-foreground">
              Bill without solar minus the bill you paid. <Link href="/bills" className="underline">Bill details</Link>
            </p>
          </>
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
