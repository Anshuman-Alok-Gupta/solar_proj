/**
 * Typed API client for the FastAPI backend.
 * Token is kept in localStorage (single-user household app); every request is scoped
 * to the selected plant by the caller.
 */

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const TOKEN_KEY = "solar.token";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function api<T>(
  path: string,
  opts: { method?: string; body?: unknown; query?: Query; form?: FormData; raw?: boolean; blob?: boolean } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }
  const res = await fetch(`${API_BASE}/api${path}${qs(opts.query)}`, {
    method: opts.method ?? (body ? "POST" : "GET"),
    headers,
    body,
  });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/login") && !path.startsWith("/invitations/")) {
    setToken(null);
    if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
  }
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail ?? j;
    } catch {
      /* non-json */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  if (opts.blob) return (await res.blob()) as T;
  if (opts.raw) return (await res.text()) as T;
  return (await res.json()) as T;
}

// ---------------------------------------------------------------- types (subset used by the UI)

export type PlantRole = "owner" | "editor" | "viewer";

/** Authenticated file download (the API needs the bearer token, so a plain link would 401). */
export async function downloadFile(path: string, filename: string): Promise<void> {
  const blob = await api<Blob>(path, { blob: true });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export interface Member {
  user_id: number;
  email: string;
  full_name: string | null;
  role: Exclude<PlantRole, "owner">;
  created_at: string;
}

export interface Invitation {
  id: number;
  email: string;
  role: Exclude<PlantRole, "owner">;
  status: "pending" | "accepted" | "revoked" | "expired";
  expires_at: string;
  accepted_at: string | null;
  created_at: string;
  token?: string;
}

export interface InvitationInfo {
  email: string;
  plant_name: string;
  role: Exclude<PlantRole, "owner">;
  invited_by: string | null;
  expires_at: string;
  status: Invitation["status"];
  account_exists: boolean;
}

export interface User {
  id: number;
  email: string;
  full_name: string | null;
  is_superuser: boolean;
}

export interface Plant {
  id: number;
  name: string;
  capacity_kw: number | null;
  latitude: number | null;
  longitude: number | null;
  timezone: string;
  panel_tilt_deg: number | null;
  panel_azimuth_deg: number | null;
  installation_date: string | null;
  installation_cost: number | null;
  currency: string;
  tariff_import_per_kwh: number | null;
  tariff_export_per_kwh: number | null;
  inverter_manufacturer: string | null;
  inverter_model: string | null;
  monitoring_platform: string | null;
  sems_station_id: string | null;
  is_demo: boolean;
  notes: string | null;
  /** The signed-in user's access to this plant. */
  access_role?: PlantRole;
  inverters: { id: number; serial_number: string; name: string | null; model: string | null; rated_power_kw: number | null }[];
}

export interface Summary {
  today: string;
  timezone: string;
  capacity_kw: number | null;
  has_data: boolean;
  data_range: { first: string; last: string; days: number } | null;
  today_kwh: number | null;
  today_is_partial: boolean;
  yesterday_kwh: number | null;
  last_7_days_kwh: number | null;
  last_30_days_kwh: number | null;
  avg_daily_30d_kwh: number | null;
  specific_yield_30d_kwh_per_kw: number | null;
  this_month_kwh: number | null;
  last_month_kwh: number | null;
  lifetime_kwh: number | null;
  best_day: { date: string; kwh: number } | null;
  latest_reading: { ts: string; power_w: number | null; temperature_c: number | null; source: string; age_minutes: number; is_live: boolean } | null;
  last_successful_sems_sync: string | null;
  open_anomalies: number;
  next_day_forecast: { target_date: string; predicted_kwh: number; lower_kwh: number | null; upper_kwh: number | null; weather_input_kind: string; issued_at: string } | null;
}

export interface DailyItem {
  date: string;
  generation_kwh: number;
  peak_power_w: number | null;
  source: string;
  is_complete: boolean;
  quality_flags: string[] | null;
  gti_wh: number | null;
  ghi_mj: number | null;
  sunshine_s: number | null;
  cloud_cover: number | null;
  temp_max: number | null;
  temp_mean: number | null;
  precip: number | null;
  specific_yield?: number | null;
}

export interface SyncLog {
  id: number;
  source: string;
  mode: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  records_fetched: number;
  records_inserted: number;
  records_updated: number;
  message: string | null;
  error: string | null;
  details: Record<string, unknown> | null;
}

export interface Anomaly {
  id: number;
  date: string;
  category: string;
  detector: string;
  severity: "info" | "low" | "medium" | "high";
  status: "open" | "acknowledged" | "resolved";
  title: string;
  description: string | null;
  actual_kwh: number | null;
  expected_kwh: number | null;
  deviation_kwh: number | null;
  deviation_pct: number | null;
  score: number | null;
  evidence: Record<string, unknown> | null;
  recommended_checks: string[] | null;
  model_version_id: number | null;
}

export interface ModelVersion {
  id: number;
  algorithm: string;
  version_tag: string;
  trained_at: string;
  is_active: boolean;
  is_baseline: boolean;
  metrics: { train?: Metrics; validation?: Metrics; test?: Metrics } | null;
  feature_names: string[] | null;
  feature_importance: Record<string, number> | null;
  hyperparameters: Record<string, unknown> | null;
  training_start: string | null;
  training_end: string | null;
  test_start: string | null;
  test_end: string | null;
  row_counts: Record<string, number> | null;
  data_sources: string[] | null;
  notes: string | null;
  validation_predictions?: PredRow[];
  test_predictions?: PredRow[];
  folds?: Record<string, unknown>[];
  residual_quantiles?: Record<string, number> | null;
}
export interface Metrics {
  mae: number | null;
  rmse: number | null;
  r2: number | null;
  smape: number | null;
  n: number;
}
export interface PredRow {
  date: string;
  actual: number;
  predicted: number;
  fold?: number;
}

export interface ForecastItem {
  target_date: string;
  issued_at: string;
  horizon_days: number;
  predicted_kwh: number;
  lower_kwh: number | null;
  upper_kwh: number | null;
  actual_kwh: number | null;
  error_kwh: number | null;
  weather_input_kind: string;
}

export interface ImportJob {
  id: number;
  filename: string;
  file_format: string;
  sheet_name: string | null;
  status: string;
  inspection: Inspection | null;
  column_mapping: Mapping | null;
  quality_report: QualityReport | null;
  rows_total: number | null;
  rows_valid: number | null;
  rows_rejected: number | null;
  rows_inserted: number | null;
  rows_updated: number | null;
  rows_duplicate: number | null;
  timezone: string | null;
  error: string | null;
  created_at: string;
  committed_at: string | null;
}
export interface Inspection {
  layout: string;
  sheets: string[];
  notes: string[];
  rows: number;
  granularity: { kind: string; median_step_minutes: number | null; first?: string; last?: string; span_days?: number };
  columns: { name: string; non_null: number; null_ratio: number; samples: string[]; min: number | null; max: number | null; suggested_role: string | null; suggested_unit: string | null; confidence: number }[];
  suggested_mapping: Mapping;
  preview: Record<string, string | null>[];
}
export interface Mapping {
  columns: Record<string, { column: string; unit: string | null }>;
  timezone?: string | null;
  dayfirst?: boolean | null;
}
export interface QualityReport {
  kind: string;
  status: "ok" | "warn" | "fail";
  checks: { name: string; status: string; detail: string; count: number }[];
  flag_counts: Record<string, number>;
  rejected: { row: number; reason: string; value: string }[];
  rejected_count: number;
  notes: string[];
  summary?: Record<string, unknown>;
  daily_summary?: Record<string, unknown>;
  commit_result?: Record<string, unknown>;
}

export interface Capabilities {
  sems_configured: boolean;
  sems_sync_enabled: boolean;
  weather_sync_enabled: boolean;
  llm_configured: boolean;
  llm_provider: string;
  llm_model: string | null;
  app_env: string;
}

// ---------------------------------------------------------------- endpoint helpers

export const endpoints = {
  login: (email: string, password: string) => api<{ access_token: string }>("/auth/login", { body: { email, password } }),
  me: () => api<User>("/auth/me"),
  capabilities: () => api<Capabilities>("/system/capabilities"),
  plants: () => api<Plant[]>("/plants"),
  plant: (id: number) => api<Plant>(`/plants/${id}`),
  updatePlant: (id: number, body: Partial<Plant>) => api<Plant>(`/plants/${id}`, { method: "PATCH", body }),
  createPlant: (body: Partial<Plant>) => api<Plant>("/plants", { body }),
  coverage: (id: number) => api<{ daily: { source: string; days: number; first_date: string; last_date: string; total_kwh: number }[]; readings: { source: string; count: number; first_ts: string; last_ts: string }[]; has_real_data: boolean; has_any_data: boolean }>(`/plants/${id}/data-coverage`),
  summary: (id: number) => api<Summary>(`/plants/${id}/analytics/summary`),
  daily: (id: number, q: Query) => api<{ items: DailyItem[]; total_kwh: number; count: number }>(`/plants/${id}/analytics/daily`, { query: q }),
  monthly: (id: number) => api<{ items: Record<string, number | string>[] }>(`/plants/${id}/analytics/monthly`),
  weekly: (id: number, weeks = 26) => api<{ items: Record<string, number | string>[] }>(`/plants/${id}/analytics/weekly`, { query: { weeks } }),
  distribution: (id: number, q?: Query) => api<{ count: number; bins: { from_kwh: number; to_kwh: number; days: number }[]; stats: Record<string, number> | null }>(`/plants/${id}/analytics/distribution`, { query: q }),
  weatherCorrelation: (id: number, q?: Query) => api<{ count: number; correlations: Record<string, number | null>; items: DailyItem[]; note: string }>(`/plants/${id}/analytics/weather-correlation`, { query: q }),
  compare: (id: number) => api<{ periods: { name: string; start: string; end: string; days_with_data: number; total_kwh: number | null; avg_daily_kwh: number | null }[] }>(`/plants/${id}/analytics/compare`),
  intraday: (id: number, day?: string) => api<{ day: string; readings: { ts: string; local_time: string; power_w: number | null; energy_kwh: number | null; temperature_c: number | null; source: string }[]; weather_hourly: { local_time: string; ghi: number | null; gti: number | null; cloud_cover: number | null; temperature: number | null }[]; sources: string[] }>(`/plants/${id}/analytics/intraday`, { query: { day } }),
  savings: (id: number, q?: Query) => api<Record<string, unknown>>(`/plants/${id}/analytics/savings`, { query: q }),
  savingsMonthly: (id: number) => api<{ items: Record<string, number | string>[]; currency?: string; missing_inputs?: string[] }>(`/plants/${id}/analytics/savings/monthly`),
  weatherDaily: (id: number, q: Query) => api<{ items: Record<string, number | string | null>[] }>(`/plants/${id}/weather/daily`, { query: q }),
  weatherSync: (id: number, mode = "incremental") => api<SyncLog>(`/plants/${id}/weather/sync`, { method: "POST", query: { mode } }),
  weatherLogs: (id: number) => api<SyncLog[]>(`/plants/${id}/weather/sync-logs`),
  semsStatus: (id: number) => api<Record<string, unknown>>(`/plants/${id}/sems/status`),
  semsTest: (id: number) => api<Record<string, unknown>>(`/plants/${id}/sems/test-connection`, { method: "POST" }),
  semsSync: (id: number, mode = "incremental") => api<SyncLog>(`/plants/${id}/sems/sync`, { method: "POST", query: { mode } }),
  semsLogs: (id: number) => api<SyncLog[]>(`/plants/${id}/sems/sync-logs`),
  imports: (id: number) => api<ImportJob[]>(`/plants/${id}/imports`),
  importUpload: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return api<ImportJob>(`/plants/${id}/imports`, { method: "POST", form });
  },
  importPreview: (id: number, job: number, mapping: Mapping, sheet_name?: string | null) => api<{ kind: string; quality_report: QualityReport; preview_rows: Record<string, unknown>[]; daily_preview: Record<string, unknown>[] }>(`/plants/${id}/imports/${job}/preview`, { body: { mapping, sheet_name } }),
  importCommit: (id: number, job: number) => api<ImportJob>(`/plants/${id}/imports/${job}/commit`, { method: "POST" }),
  importDelete: (id: number, job: number) => api<void>(`/plants/${id}/imports/${job}`, { method: "DELETE" }),
  importRoles: (id: number) => api<{ roles: { role: string; label: string; kind: string; units?: string[] }[] }>(`/plants/${id}/imports/roles`),
  train: (id: number) => api<Record<string, unknown>>(`/plants/${id}/ml/train`, { body: {} }),
  models: (id: number) => api<{ active_model_id: number | null; latest_run_prefix: string | null; items: ModelVersion[] }>(`/plants/${id}/ml/models`),
  model: (id: number, mid: number) => api<ModelVersion>(`/plants/${id}/ml/models/${mid}`),
  activate: (id: number, mid: number) => api<{ active_model_id: number }>(`/plants/${id}/ml/models/${mid}/activate`, { method: "POST" }),
  explain: (id: number, mid?: number) => api<{ model_version_id: number; algorithm: string; shap: { base_value: number; features: { feature: string; label: string; mean_abs_shap: number }[]; samples: { values: Record<string, number | null>; shap: Record<string, number> }[] } | null; native_importance: Record<string, number> | null; note?: string }>(`/plants/${id}/ml/explain`, { query: { model_id: mid } }),
  forecasts: (id: number, days = 60) => api<{ active_model_id: number | null; items: ForecastItem[]; live_forecast_mae_kwh: number | null; scored_days: number; note: string }>(`/plants/${id}/forecasts`, { query: { days } }),
  issueForecast: (id: number, days = 3) => api<{ issued: Record<string, unknown>[]; notes: string[] }>(`/plants/${id}/forecasts/issue`, { method: "POST", query: { days } }),
  backtest: (id: number, q?: Query) => api<{ algorithm: string; items: { date: string; expected_kwh: number; actual_kwh: number | null; residual_kwh: number | null }[] }>(`/plants/${id}/forecasts/backtest`, { query: q }),
  anomalies: (id: number, q?: Query) => api<{ count: number; by_severity: Record<string, number>; items: Anomaly[] }>(`/plants/${id}/anomalies`, { query: q }),
  detect: (id: number) => api<Record<string, unknown>>(`/plants/${id}/anomalies/detect`, { method: "POST" }),
  anomalyStatus: (id: number, aid: number, status: string) => api<Anomaly>(`/plants/${id}/anomalies/${aid}`, { method: "PATCH", body: { status } }),
  reports: (id: number) => api<{ items: Report[] }>(`/plants/${id}/reports`),
  reportPreview: (id: number, kind: string) => api<Report>(`/plants/${id}/reports/preview`, { query: { kind } }),
  reportGenerate: (id: number, kind: string) => api<Report>(`/plants/${id}/reports/generate`, { method: "POST", query: { kind } }),
  reportCsvUrl: (id: number, rid: number) => `${API_BASE}/api/plants/${id}/reports/${rid}/csv`,
  reportDownload: (id: number, rid: number, format: "csv" | "pdf") => downloadFile(`/plants/${id}/reports/${rid}/${format}`, `solar_report_${rid}.${format}`),
  reportDelete: (id: number, rid: number) => api<void>(`/plants/${id}/reports/${rid}`, { method: "DELETE" }),
  members: (id: number) => api<{ owner: { id: number; email: string; full_name: string | null } | null; members: Member[]; invitations: Invitation[] }>(`/plants/${id}/members`),
  invite: (id: number, email: string, role: Member["role"]) => api<Invitation>(`/plants/${id}/invitations`, { body: { email, role } }),
  revokeInvite: (id: number, inviteId: number) => api<void>(`/plants/${id}/invitations/${inviteId}`, { method: "DELETE" }),
  setMemberRole: (id: number, userId: number, role: Member["role"]) => api<{ role: string }>(`/plants/${id}/members/${userId}`, { method: "PATCH", body: { role } }),
  removeMember: (id: number, userId: number) => api<void>(`/plants/${id}/members/${userId}`, { method: "DELETE" }),
  invitation: (token: string) => api<InvitationInfo>(`/invitations/${encodeURIComponent(token)}`),
  acceptInvitation: (token: string, password: string, full_name?: string) => api<{ access_token: string }>(`/invitations/${encodeURIComponent(token)}/accept`, { body: { password, full_name } }),
  changePassword: (current_password: string, new_password: string) => api<void>("/auth/change-password", { body: { current_password, new_password } }),
  aiStatus: (id: number) => api<{ configured: boolean; provider: string; model: string | null; tool_mode: string; suggested_questions: string[]; setup_hint: string | null }>(`/plants/${id}/ai/status`),
  aiChat: (id: number, message: string, conversation_id?: number | null) => api<{ conversation_id: number; reply: string; tool_calls: { tool: string; input: Record<string, unknown>; ok: boolean; error?: string }[]; usage: Record<string, unknown> }>(`/plants/${id}/ai/chat`, { body: { message, conversation_id } }),
  aiConversations: (id: number) => api<{ items: { id: number; title: string | null; updated_at: string; message_count: number }[] }>(`/plants/${id}/ai/conversations`),
  aiConversation: (id: number, cid: number) => api<{ id: number; title: string | null; messages: { id: number; role: string; content: string; tool_calls: { tool: string; input: Record<string, unknown>; ok: boolean }[] | null; created_at: string }[] }>(`/plants/${id}/ai/conversations/${cid}`),
  aiDelete: (id: number, cid: number) => api<void>(`/plants/${id}/ai/conversations/${cid}`, { method: "DELETE" }),
};

export interface Report {
  id?: number;
  kind?: string;
  title: string;
  period_start: string;
  period_end: string;
  summary_text: string;
  content: Record<string, unknown>;
  created_at?: string;
}
