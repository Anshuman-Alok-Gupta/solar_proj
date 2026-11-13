"use client";

import { ChevronDown, ChevronRight, FileUp, Lock, Pencil, PenLine, Trash2 } from "lucide-react";
import { Fragment, useRef, useState } from "react";
import { toast } from "sonner";

import { BillForm } from "@/components/bills/bill-form";
import { EmptyState, ErrorState, LoadingBlock, PageHeader } from "@/components/state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { type Bill, type BillFields, type BillsSummary, endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { type BillFormState, billMonthLabel, toForm } from "@/lib/bills";
import { kwh, money, num, pct, shortDate } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";

type Editing = { form: BillFormState; warnings?: string[]; replaces?: boolean; billId?: number; key: number };

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-md border p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

function Summary({ s }: { s: BillsSummary }) {
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <Stat label="Saved (from bills)" value={money(s.total_savings)} sub={`${s.bills_with_savings} of ${s.bills} bill(s)`} />
      <Stat label="Average per month" value={money(s.avg_monthly_savings)} />
      <Stat label="Bills without solar vs actual" value={`${money(s.total_without_solar)} → ${money(s.total_actual)}`} />
      <Stat
        label="Solar bank balance"
        value={kwh(s.bank_balance_kwh, 0)}
        sub={s.bank_value_at_settlement != null ? `at least ${money(s.bank_value_at_settlement)} if unused at year end` : undefined}
      />
    </div>
  );
}

function Details({ b }: { b: Bill }) {
  const a = b.analysis;
  const w = a.without_solar;
  return (
    <div className="grid gap-4 py-2 text-sm md:grid-cols-3">
      <dl className="grid grid-cols-2 gap-y-1">
        <dt className="text-muted-foreground">Generation</dt>
        <dd className="tabular-nums">{kwh(a.generation_kwh, 0)} <span className="text-xs text-muted-foreground">({a.generation_source === "bill" ? "bill meter" : "inverter"})</span></dd>
        <dt className="text-muted-foreground">Inverter, same days</dt>
        <dd className="tabular-nums">{kwh(a.inverter_generation.kwh, 0)}{a.inverter_vs_meter_pct != null && <span className="text-xs text-muted-foreground"> ({a.inverter_vs_meter_pct > 0 ? "+" : ""}{num(a.inverter_vs_meter_pct, 1)}%)</span>}</dd>
        <dt className="text-muted-foreground">Used directly</dt>
        <dd className="tabular-nums">{kwh(a.self_consumed_kwh ?? null, 0)} <span className="text-xs text-muted-foreground">({pct(a.self_consumption_pct ?? null)} of generation)</span></dd>
        <dt className="text-muted-foreground">Covered by solar</dt>
        <dd className="tabular-nums">{pct(a.solar_share_pct ?? null)} of use</dd>
      </dl>
      <dl className="grid grid-cols-2 gap-y-1">
        <dt className="col-span-2 font-medium">Bill without solar</dt>
        {w ? (
          <>
            <dt className="text-muted-foreground">Energy charge</dt><dd className="tabular-nums">{money(w.energy)}</dd>
            <dt className="text-muted-foreground">Fuel adjustment</dt><dd className="tabular-nums">{money(w.fac)}</dd>
            <dt className="text-muted-foreground">Wheeling</dt><dd className="tabular-nums">{money(w.wheeling)}</dd>
            <dt className="text-muted-foreground">Electricity duty</dt><dd className="tabular-nums">{money(w.duty)}</dd>
            <dt className="text-muted-foreground">Fixed + other</dt><dd className="tabular-nums">{money(w.fixed_and_other)}</dd>
            <dt className="font-medium">Total</dt><dd className="font-medium tabular-nums">{money(w.total)}</dd>
          </>
        ) : (
          <dd className="col-span-2 text-muted-foreground">Not enough information.</dd>
        )}
      </dl>
      <dl className="grid grid-cols-2 gap-y-1">
        <dt className="col-span-2 font-medium">Solar bank</dt>
        <dt className="text-muted-foreground">Export used this bill</dt><dd className="tabular-nums">{kwh(a.bank.offset_kwh, 0)}</dd>
        <dt className="text-muted-foreground">Added to bank</dt><dd className="tabular-nums">{kwh(a.bank.added_kwh, 0)}</dd>
        <dt className="text-muted-foreground">Bank balance</dt><dd className="tabular-nums">{kwh(a.bank.balance_kwh, 0)}</dd>
        {a.bank.added_value_at_settlement != null && (
          <><dt className="text-muted-foreground">Added, at year-end rate</dt><dd className="tabular-nums">{money(a.bank.added_value_at_settlement)}</dd></>
        )}
        {a.check && (
          <><dt className="text-muted-foreground">Model check</dt><dd className="tabular-nums">{money(a.check.computed_variable_charges)} vs {money(a.check.variable_charges_on_bill)} on bill</dd></>
        )}
      </dl>
      {!!a.notes.length && (
        <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground md:col-span-3">
          {a.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function BillsPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const canWrite = (plant?.access_role ?? "owner") !== "viewer";
  const q = useApi(pid ? () => endpoints.bills(pid) : null, [pid]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  if (!plant || pid === null) return <EmptyState title="No plant selected" description="Create a plant in Settings first." />;
  const bills = q.data?.bills ?? [];
  const lastTariff = bills.find((b) => b.tariff?.slabs?.length)?.tariff ?? null;

  async function upload(file: File) {
    setBusy("parse");
    try {
      const r = await endpoints.billParse(pid!, file);
      setEditing({ form: toForm(r.fields), warnings: r.warnings, replaces: r.existing_bill_id != null, key: Date.now() });
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function save(payload: BillFields) {
    setBusy("save");
    try {
      const b = editing?.billId ? await endpoints.billUpdate(pid!, editing.billId, payload) : await endpoints.billSave(pid!, payload);
      toast.success(b.analysis.savings != null ? `Saved: ${money(b.analysis.savings)} saved on this bill` : "Bill saved");
      setEditing(null);
      setOpen(b.id);
      await q.refetch();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function remove(b: Bill) {
    if (!window.confirm(`Delete the ${billMonthLabel(b.bill_month)} bill?`)) return;
    setBusy(`del-${b.id}`);
    try {
      await endpoints.billDelete(pid!, b.id);
      await q.refetch();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Electricity bills"
        description="Add your MSEDCL bills to measure what solar actually saves, using the bill's own units, charges and tariff."
        actions={
          canWrite && !editing ? (
            <>
              <input ref={fileRef} type="file" accept="application/pdf,.pdf" className="hidden" onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
              <Button onClick={() => fileRef.current?.click()} disabled={busy === "parse"}>
                <FileUp className="mr-1.5 h-4 w-4" /> {busy === "parse" ? "Reading…" : "Upload bill PDF"}
              </Button>
              <Button variant="outline" onClick={() => setEditing({ form: toForm(null, lastTariff), key: Date.now() })}>
                <PenLine className="mr-1.5 h-4 w-4" /> Enter manually
              </Button>
            </>
          ) : null
        }
      />

      <Alert>
        <Lock className="h-4 w-4" />
        <AlertTitle>Your personal details stay out of it</AlertTitle>
        <AlertDescription>
          The PDF is read by this app&apos;s own server, not by AI, and is discarded straight away. Only the units, charges, dates and
          tariff rates are saved. Your name, address, consumer number and meter number are never read or stored, and bills are not
          shared with the AI analyst.
        </AlertDescription>
      </Alert>

      {editing && (
        <Card>
          <CardHeader>
            <CardTitle>{editing.billId ? "Edit bill" : editing.form.source === "pdf" ? "Check the bill" : "New bill"}</CardTitle>
            <CardDescription>Units are kWh as printed on the bill. Leave a field blank if your bill doesn&apos;t show it.</CardDescription>
          </CardHeader>
          <CardContent>
            <BillForm
              key={editing.key}
              initial={editing.form}
              warnings={editing.warnings}
              replaces={editing.replaces}
              saving={busy === "save"}
              onSave={save}
              onCancel={() => setEditing(null)}
            />
          </CardContent>
        </Card>
      )}

      {q.loading && !q.data ? (
        <LoadingBlock lines={4} />
      ) : q.error ? (
        <ErrorState message={q.error} onRetry={q.refetch} />
      ) : !bills.length ? (
        !editing && <EmptyState title="No bills yet" description="Upload the PDF you download from the Mahavitaran website or app, or type the numbers in." />
      ) : (
        <>
          {q.data && <Summary s={q.data.summary} />}
          <Card>
            <CardHeader>
              <CardTitle>Bills</CardTitle>
              <CardDescription>Savings = what the bill would have been without solar, minus what it actually charged (arrears excluded).</CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
                    <TableHead>Month</TableHead>
                    <TableHead>Readings</TableHead>
                    <TableHead className="text-right">Import / export</TableHead>
                    <TableHead className="text-right">Used</TableHead>
                    <TableHead className="text-right">Without solar</TableHead>
                    <TableHead className="text-right">Charged</TableHead>
                    <TableHead className="text-right">Saved</TableHead>
                    {canWrite && <TableHead className="w-20" />}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {bills.map((b) => (
                    <Fragment key={b.id}>
                      <TableRow className="cursor-pointer" onClick={() => setOpen(open === b.id ? null : b.id)}>
                        <TableCell>{open === b.id ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}</TableCell>
                        <TableCell className="font-medium">{billMonthLabel(b.bill_month)}</TableCell>
                        <TableCell className="whitespace-nowrap text-muted-foreground">{shortDate(b.reading_from)} – {shortDate(b.reading_to)}</TableCell>
                        <TableCell className="text-right tabular-nums">{num(b.import_kwh, 0)} / {num(b.export_kwh, 0)}</TableCell>
                        <TableCell className="text-right tabular-nums">{kwh(b.analysis.consumption_kwh ?? null, 0)}</TableCell>
                        <TableCell className="text-right tabular-nums">{money(b.analysis.without_solar?.total ?? null)}</TableCell>
                        <TableCell className="text-right tabular-nums">{money(b.analysis.actual_bill)}</TableCell>
                        <TableCell className="text-right font-medium tabular-nums">{money(b.analysis.savings ?? null)}</TableCell>
                        {canWrite && (
                          <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                            <Button variant="ghost" size="icon" aria-label="Edit bill" onClick={() => setEditing({ form: toForm(b), billId: b.id, key: Date.now() })}>
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button variant="ghost" size="icon" aria-label="Delete bill" disabled={busy === `del-${b.id}`} onClick={() => remove(b)}>
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </TableCell>
                        )}
                      </TableRow>
                      {open === b.id && (
                        <TableRow>
                          <TableCell />
                          <TableCell colSpan={canWrite ? 8 : 7}><Details b={b} /></TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
