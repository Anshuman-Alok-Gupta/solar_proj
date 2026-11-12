"use client";

import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ChartCard } from "@/components/charts/common";
import { ActualVsPredictedChart, ImportanceChart, ResidualChart } from "@/components/charts/generation-charts";
import { ChartSkeleton, EmptyState, ErrorState, LoadingBlock, PageHeader } from "@/components/state";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { type ModelVersion, endpoints } from "@/lib/api";
import { useApp } from "@/lib/app-context";
import { ALGO_LABEL, dateTime, num, shortDate } from "@/lib/format";
import { errorMessage, useApi } from "@/lib/use-api";
import { cn } from "@/lib/utils";

const m3 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

export default function ModelLabPage() {
  const { plant } = useApp();
  const pid = plant?.id ?? null;
  const models = useApi(pid ? () => endpoints.models(pid) : null, [pid]);
  const [picked, setSelected] = useState<number | null>(null);
  const selected = picked ?? models.data?.active_model_id ?? models.data?.items[0]?.id ?? null;
  const detail = useApi(pid && selected ? () => endpoints.model(pid, selected) : null, [pid, selected]);
  const explain = useApi(pid && selected ? () => endpoints.explain(pid, selected) : null, [pid, selected]);
  const [training, setTraining] = useState(false);
  const [trainResult, setTrainResult] = useState<Record<string, unknown> | null>(null);

  if (!plant) return <EmptyState title="No plant selected" />;

  const latestRun = models.data ? models.data.items.filter((m) => models.data?.latest_run_prefix && m.version_tag.startsWith(models.data.latest_run_prefix)) : [];
  const runs = models.data ? Array.from(new Set(models.data.items.map((m) => m.version_tag.slice(0, m.version_tag.lastIndexOf("_"))))) : [];

  async function train() {
    if (!pid) return;
    setTraining(true);
    try {
      const r = await endpoints.train(pid);
      setTrainResult(r);
      toast.success(`Training complete: ${(r.selected as { algorithm: string }).algorithm} selected`);
      await models.refetch();
      setSelected((r.selected as { model_version_id: number }).model_version_id);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setTraining(false);
    }
  }

  async function activate(m: ModelVersion) {
    if (!pid) return;
    try {
      await endpoints.activate(pid, m.id);
      toast.success(`${ALGO_LABEL[m.algorithm] ?? m.algorithm} is now active`);
      await models.refetch();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  }

  const sel = detail.data;

  return (
    <div>
      <PageHeader
        title="Model Lab"
        description="Compare forecasting algorithms on chronological out-of-sample data. Metrics are computed, never typed in."
        actions={<Button onClick={train} disabled={training}><FlaskConical className={cn("h-4 w-4", training && "animate-pulse")} /> {training ? "Training…" : "Train all models"}</Button>}
      />

      {trainResult && (
        <Alert className="mb-4">
          <AlertTitle>Latest training run</AlertTitle>
          <AlertDescription>
            {String((trainResult.rows as number) ?? "")} days · test size {String(trainResult.test_size)} · selected {(trainResult.selected as { algorithm: string }).algorithm} ({(trainResult.selected as { policy: string }).policy}).{" "}
            {(trainResult.caveats as string[])?.join(" ")}
          </AlertDescription>
        </Alert>
      )}

      {models.error && <ErrorState message={models.error} onRetry={models.refetch} className="mb-4" />}
      {models.loading && !models.data ? <LoadingBlock lines={5} /> : models.data && models.data.items.length === 0 ? (
        <EmptyState title="No models trained yet" description="Training needs at least 20 complete days with weather. Click 'Train all models' to evaluate baselines, Random Forest, XGBoost, LightGBM and CatBoost." />
      ) : models.data ? (
        <>
          <Card>
            <CardHeader><CardTitle className="text-base">Model comparison (latest run · {runs.length} run{runs.length === 1 ? "" : "s"} total)</CardTitle></CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Algorithm</TableHead>
                    <TableHead className="text-right">Val MAE</TableHead>
                    <TableHead className="text-right">Val RMSE</TableHead>
                    <TableHead className="text-right">Val R²</TableHead>
                    <TableHead className="text-right">Test MAE</TableHead>
                    <TableHead className="text-right">Test RMSE</TableHead>
                    <TableHead className="text-right">Test R²</TableHead>
                    <TableHead>Trained</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...latestRun].sort((a, b) => (a.metrics?.validation?.mae ?? 1e9) - (b.metrics?.validation?.mae ?? 1e9)).map((m) => (
                    <TableRow key={m.id} className={cn("cursor-pointer", selected === m.id && "bg-muted/60")} onClick={() => setSelected(m.id)}>
                      <TableCell className="font-medium">{ALGO_LABEL[m.algorithm] ?? m.algorithm} {m.is_active && <Badge className="ml-1">active</Badge>} {m.is_baseline && <Badge variant="outline" className="ml-1">baseline</Badge>}</TableCell>
                      <TableCell className="text-right tabular-nums">{m3(m.metrics?.validation?.mae)}</TableCell>
                      <TableCell className="text-right tabular-nums">{m3(m.metrics?.validation?.rmse)}</TableCell>
                      <TableCell className="text-right tabular-nums">{m3(m.metrics?.validation?.r2)}</TableCell>
                      <TableCell className="text-right tabular-nums">{m3(m.metrics?.test?.mae)}</TableCell>
                      <TableCell className="text-right tabular-nums">{m3(m.metrics?.test?.rmse)}</TableCell>
                      <TableCell className="text-right tabular-nums">{m3(m.metrics?.test?.r2)}</TableCell>
                      <TableCell className="text-muted-foreground">{dateTime(m.trained_at)}</TableCell>
                      <TableCell className="text-right">{!m.is_active && <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); activate(m); }}>Activate</Button>}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="mt-2 text-xs text-muted-foreground">Validation = pooled expanding-window cross-validation; Test = chronological hold-out never used for selection. MAE/RMSE in kWh. Both use observed weather (backtest), so real forecasts will be less accurate.</p>
            </CardContent>
          </Card>

          {sel && (
            <div className="mt-4">
              <Tabs defaultValue="predictions">
                <TabsList className="mb-4">
                  <TabsTrigger value="predictions">Actual vs predicted</TabsTrigger>
                  <TabsTrigger value="residuals">Residuals</TabsTrigger>
                  <TabsTrigger value="importance">Feature importance</TabsTrigger>
                  <TabsTrigger value="shap">SHAP</TabsTrigger>
                  <TabsTrigger value="details">Details</TabsTrigger>
                </TabsList>
                <TabsContent value="predictions" className="grid gap-4 lg:grid-cols-2">
                  <ChartCard title={`Validation folds · ${ALGO_LABEL[sel.algorithm] ?? sel.algorithm}`} description="Out-of-sample predictions pooled across expanding-window folds">
                    {detail.loading ? <ChartSkeleton /> : sel.validation_predictions?.length ? <ActualVsPredictedChart rows={sel.validation_predictions} /> : <EmptyState title="No validation predictions" />}
                  </ChartCard>
                  <ChartCard title="Hold-out test" description={`${shortDate(sel.test_start)} – ${shortDate(sel.test_end)}`}>
                    {detail.loading ? <ChartSkeleton /> : sel.test_predictions?.length ? <ActualVsPredictedChart rows={sel.test_predictions} /> : <EmptyState title="No test predictions" />}
                  </ChartCard>
                </TabsContent>
                <TabsContent value="residuals" className="grid gap-4 lg:grid-cols-2">
                  <ChartCard title="Validation residuals" description="Actual − predicted per day">{sel.validation_predictions?.length ? <ResidualChart rows={sel.validation_predictions} /> : <EmptyState title="—" />}</ChartCard>
                  <ChartCard title="Test residuals">{sel.test_predictions?.length ? <ResidualChart rows={sel.test_predictions} /> : <EmptyState title="—" />}</ChartCard>
                  {sel.residual_quantiles && (
                    <Card className="lg:col-span-2"><CardContent className="p-4 text-sm"><span className="font-medium">Residual quantiles (kWh):</span> {Object.entries(sel.residual_quantiles).map(([q, v]) => <Badge key={q} variant="outline" className="ml-1 font-normal">p{Math.round(Number(q) * 100)}: {num(v, 2)}</Badge>)} <span className="ml-2 text-muted-foreground">used for the empirical 10–90% forecast interval</span></CardContent></Card>
                  )}
                </TabsContent>
                <TabsContent value="importance">
                  <ChartCard title="Model-native feature importance" description="Normalised importance from the fitted estimator (baselines have none)">
                    {sel.feature_importance ? <ImportanceChart items={Object.entries(sel.feature_importance).map(([f, v]) => ({ label: f, value: v }))} /> : <EmptyState title="Not applicable" description="Baseline models have no learned feature importance." />}
                  </ChartCard>
                </TabsContent>
                <TabsContent value="shap">
                  <ChartCard title="SHAP global importance" description="Mean |SHAP| per feature on the training frame (TreeExplainer)">
                    {explain.loading ? <ChartSkeleton /> : explain.data?.shap ? <ImportanceChart items={explain.data.shap.features.map((f) => ({ label: f.label, value: f.mean_abs_shap }))} /> : <EmptyState title="No SHAP for this model" description={explain.data?.note ?? "SHAP is available for tree models only."} />}
                  </ChartCard>
                </TabsContent>
                <TabsContent value="details">
                  <Card>
                    <CardContent className="grid gap-4 p-4 text-sm md:grid-cols-2">
                      <dl className="grid grid-cols-2 gap-y-1">
                        <dt className="text-muted-foreground">Version</dt><dd className="font-mono text-xs">{sel.version_tag}</dd>
                        <dt className="text-muted-foreground">Training period</dt><dd>{shortDate(sel.training_start)} – {shortDate(sel.training_end)}</dd>
                        <dt className="text-muted-foreground">Test period</dt><dd>{shortDate(sel.test_start)} – {shortDate(sel.test_end)}</dd>
                        <dt className="text-muted-foreground">Rows</dt><dd>{JSON.stringify(sel.row_counts)}</dd>
                        <dt className="text-muted-foreground">Data sources</dt><dd>{sel.data_sources?.join(", ")}</dd>
                        <dt className="text-muted-foreground">Train MAE</dt><dd>{m3(sel.metrics?.train?.mae)} kWh</dd>
                      </dl>
                      <div>
                        <div className="mb-1 font-medium">Hyperparameters</div>
                        <pre className="max-h-48 overflow-auto rounded bg-muted p-2 text-xs">{JSON.stringify(sel.hyperparameters, null, 1)}</pre>
                        <div className="mt-2 mb-1 font-medium">Features ({sel.feature_names?.length})</div>
                        <p className="text-xs text-muted-foreground">{sel.feature_names?.join(", ")}</p>
                        {sel.notes && <p className="mt-2 text-xs text-muted-foreground">{sel.notes}</p>}
                      </div>
                      {sel.folds && sel.folds.length > 0 && (
                        <div className="md:col-span-2">
                          <div className="mb-1 font-medium">Cross-validation folds</div>
                          <Table>
                            <TableHeader><TableRow><TableHead>Fold</TableHead><TableHead>Train rows</TableHead><TableHead>Validation</TableHead><TableHead className="text-right">MAE</TableHead><TableHead className="text-right">RMSE</TableHead></TableRow></TableHeader>
                            <TableBody>{sel.folds.map((f, i) => <TableRow key={i}><TableCell>{String(f.fold)}</TableCell><TableCell>{String(f.train_rows)}</TableCell><TableCell>{shortDate(String(f.val_start))} – {shortDate(String(f.val_end))} ({String(f.val_rows)} d)</TableCell><TableCell className="text-right tabular-nums">{m3(f.mae as number)}</TableCell><TableCell className="text-right tabular-nums">{m3(f.rmse as number)}</TableCell></TableRow>)}</TableBody>
                          </Table>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                </TabsContent>
              </Tabs>
            </div>
          )}

          {models.data.items.length > latestRun.length && (
            <Card className="mt-4">
              <CardHeader><CardTitle className="text-base">Training history</CardTitle></CardHeader>
              <CardContent>
                <Table>
                  <TableHeader><TableRow><TableHead>Trained</TableHead><TableHead>Algorithm</TableHead><TableHead className="text-right">Val MAE</TableHead><TableHead className="text-right">Test MAE</TableHead><TableHead>Rows</TableHead><TableHead></TableHead></TableRow></TableHeader>
                  <TableBody>
                    {models.data.items.map((m) => (
                      <TableRow key={m.id} className="cursor-pointer" onClick={() => setSelected(m.id)}>
                        <TableCell className="text-muted-foreground">{dateTime(m.trained_at)}</TableCell>
                        <TableCell>{ALGO_LABEL[m.algorithm] ?? m.algorithm} {m.is_active && <Badge className="ml-1">active</Badge>}</TableCell>
                        <TableCell className="text-right tabular-nums">{m3(m.metrics?.validation?.mae)}</TableCell>
                        <TableCell className="text-right tabular-nums">{m3(m.metrics?.test?.mae)}</TableCell>
                        <TableCell>{m.row_counts?.total}</TableCell>
                        <TableCell className="text-right">{!m.is_active && <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); activate(m); }}>Activate</Button>}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      ) : null}
    </div>
  );
}
