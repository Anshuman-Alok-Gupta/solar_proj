import { format, parseISO } from "date-fns";

export function kwh(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)} kWh`;
}

export function num(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

export function watts(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return v >= 1000 ? `${(v / 1000).toFixed(2)} kW` : `${Math.round(v)} W`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function money(v: number | null | undefined, currency = "INR"): string {
  if (v === null || v === undefined) return "—";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(v);
  } catch {
    return `${currency} ${v.toFixed(0)}`;
  }
}

export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return format(parseISO(iso), "d MMM");
  } catch {
    return iso;
  }
}

export function longDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return format(parseISO(iso), "EEE d MMM yyyy");
  } catch {
    return iso;
  }
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return format(parseISO(iso), "d MMM yyyy HH:mm");
  } catch {
    return iso;
  }
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const m = Math.round(ms / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

export const SOURCE_LABEL: Record<string, string> = {
  sems_api: "SEMS API",
  csv_import: "Imported file",
  synthetic: "Synthetic demo",
};

export const CATEGORY_LABEL: Record<string, string> = {
  unexpected_production_drop: "Unexpected production drop",
  possible_underperformance: "Possible underperformance",
  missing_readings: "Missing readings",
  zero_generation: "Zero generation",
  unusual_power_fluctuations: "Unusual power fluctuations",
  data_quality_issue: "Data quality issue",
  statistical_outlier: "Statistical outlier",
};

export const ALGO_LABEL: Record<string, string> = {
  baseline_persistence: "Persistence (yesterday)",
  baseline_rolling7: "7-day mean",
  baseline_irradiance: "Irradiance-scaled baseline",
  random_forest: "Random Forest",
  xgboost: "XGBoost",
  lightgbm: "LightGBM",
  catboost: "CatBoost",
};
