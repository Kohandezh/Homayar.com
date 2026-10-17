import { formatAudioHours, formatNumber } from "@/lib/format";
import type { BillingUsageSnapshot, Plan } from "@/types/platform";

/**
 * A date without a time, in the reader's calendar: subscription dates and
 * quota periods are whole days, and the date-plus-time form wraps to three
 * lines in the cards. The events table keeps the full `formatDate`.
 */
export function formatDateOnly(value: string, lang: "fa" | "en"): string {
  return new Intl.DateTimeFormat(lang === "fa" ? "fa-IR" : "en", { dateStyle: "medium" }).format(
    new Date(value),
  );
}

/** The quota metrics the page knows, in display order; any other metric follows. */
export const USAGE_METRICS = ["audio_seconds", "concurrent_jobs", "seats"] as const;

/** From this share of the limit a meter warns (compared on the exact ratio). */
export const WARNING_NUMERATOR = 80;

export type QuotaLevel = "normal" | "warning" | "danger";

export interface UsageRow {
  metric: string;
  used: number;
  reserved: number;
  /** used + reserved: what the limit is measured against. */
  consumed: number;
  limit: number | null;
  remaining: number | null;
  /** Floored and clamped to 0..100; null when there is no limit. */
  percent: number | null;
  level: QuotaLevel;
  periodStart: string;
  periodEnd: string | null;
}

function metricOrder(metric: string): number {
  const index = (USAGE_METRICS as readonly string[]).indexOf(metric);
  return index === -1 ? USAGE_METRICS.length : index;
}

/**
 * Share of the limit in whole percent, floored and clamped to 0..100. A zero
 * limit counts as full. Floored, never rounded: a quota is never shown as
 * larger than it is (79.6 percent stays 79).
 */
export function usagePercent(consumed: number, limit: number): number {
  if (limit <= 0) return 100;
  return Math.min(100, Math.max(0, Math.floor((100 * consumed) / limit)));
}

/**
 * The warning level of consumed against a limit, from the exact ratio and not
 * from the rounded percent: reached is danger, 80 percent and up is warning.
 * A zero limit is always reached (consumed >= 0).
 */
export function usageLevel(consumed: number, limit: number): QuotaLevel {
  if (consumed >= limit) return "danger";
  if (consumed * 100 >= WARNING_NUMERATOR * limit) return "warning";
  return "normal";
}

/** One row per snapshot: the known metrics first, then the rest in server order. */
export function usageRows(items: readonly BillingUsageSnapshot[]): UsageRow[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => metricOrder(a.item.metric) - metricOrder(b.item.metric) || a.index - b.index)
    .map(({ item }) => {
      const consumed = item.used + item.reserved;
      const percent = item.limit === null ? null : usagePercent(consumed, item.limit);
      const level = item.limit === null ? "normal" : usageLevel(consumed, item.limit);
      return {
        metric: item.metric,
        used: item.used,
        reserved: item.reserved,
        consumed,
        limit: item.limit,
        remaining: item.remaining,
        percent,
        level,
        periodStart: item.period_start,
        periodEnd: item.period_end,
      };
    });
}

const METRIC_LABELS: Record<string, { fa: string; en: string }> = {
  audio_seconds: { fa: "پردازش صوت", en: "Audio processing" },
  concurrent_jobs: { fa: "پردازش‌های هم‌زمان", en: "Concurrent jobs" },
  seats: { fa: "ظرفیت اعضا", en: "Seats" },
};

/** A metric's name; an unknown metric is shown as the server sent it. */
export function metricLabel(metric: string, lang: "fa" | "en"): string {
  return Object.hasOwn(METRIC_LABELS, metric) ? METRIC_LABELS[metric][lang] : metric.replaceAll("_", " ");
}

const EVENT_TYPE_LABELS: Record<string, { fa: string; en: string }> = {
  reserved: { fa: "رزرو", en: "Reserved" },
  committed: { fa: "ثبت قطعی", en: "Committed" },
  released: { fa: "آزادسازی", en: "Released" },
  adjusted: { fa: "اصلاح", en: "Adjusted" },
};

/** A usage event's type; an unknown type is shown as the server sent it. */
export function eventTypeLabel(eventType: string, lang: "fa" | "en"): string {
  return Object.hasOwn(EVENT_TYPE_LABELS, eventType)
    ? EVENT_TYPE_LABELS[eventType][lang]
    : eventType.replaceAll("_", " ");
}

const SUBJECT_LABELS: Record<string, { fa: string; en: string }> = {
  meeting: { fa: "جلسه", en: "Meeting" },
};

/** An event subject's name; an unknown type is shown as the server sent it. */
export function subjectLabel(subjectType: string, lang: "fa" | "en"): string {
  return Object.hasOwn(SUBJECT_LABELS, subjectType) ? SUBJECT_LABELS[subjectType][lang] : subjectType.replaceAll("_", " ");
}

/** An amount of a metric: audio in hours and minutes, anything else a count. */
export function formatMetricValue(metric: string, value: number, lang: "fa" | "en"): string {
  return metric === "audio_seconds" ? formatAudioHours(value, lang) : formatNumber(value, lang);
}

export type EntitlementValue =
  | { kind: "limit"; value: number }
  | { kind: "unlimited" }
  | { kind: "by_agreement" };

/**
 * What a plan grants for one metric. A number is a limit, an explicit null is
 * unlimited, and a key the plan does not name (or names with any other value)
 * is set by agreement: the catalog never invents a number.
 */
export function entitlementValue(plan: Plan, metric: string): EntitlementValue {
  if (!Object.hasOwn(plan.entitlements, metric)) return { kind: "by_agreement" };
  const value = plan.entitlements[metric];
  if (value === null) return { kind: "unlimited" };
  return typeof value === "number" ? { kind: "limit", value } : { kind: "by_agreement" };
}

/** The rows of the plan comparison: the known metrics always, then any other key, sorted. */
export function catalogMetrics(plans: readonly Plan[]): string[] {
  const others = new Set<string>();
  for (const plan of plans) {
    for (const metric of Object.keys(plan.entitlements)) {
      if (metricOrder(metric) === USAGE_METRICS.length) others.add(metric);
    }
  }
  return [...USAGE_METRICS, ...[...others].sort((a, b) => a.localeCompare(b))];
}
