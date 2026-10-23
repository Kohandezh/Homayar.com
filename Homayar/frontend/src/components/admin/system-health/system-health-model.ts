import { formatNumber } from "@/lib/format";

/**
 * Pure helpers of the system health page (HOM-232): status presentation,
 * runbook links and age text. No React here, so the rules of P4 to P7 are
 * testable on their own.
 */

/** The Chip tones HeroUI v3 offers that the status table uses (P4a). */
export type StatusTone = "success" | "warning" | "danger" | "default";

export interface StatusPresentation {
  en: string;
  fa: string;
  tone: StatusTone;
}

/** P4: every status value the API can send, with its label and tone. */
export const STATUS_PRESENTATIONS: Record<string, StatusPresentation> = {
  ok: { en: "OK", fa: "سالم", tone: "success" },
  up: { en: "Up", fa: "در دسترس", tone: "success" },
  up_to_date: { en: "Up to date", fa: "به‌روز", tone: "success" },
  degraded: { en: "Degraded", fa: "دچار اختلال", tone: "warning" },
  timeout: { en: "Timed out", fa: "بدون پاسخ", tone: "warning" },
  behind: { en: "Behind", fa: "عقب‌تر از کد", tone: "warning" },
  unstamped: { en: "Unstamped", fa: "بدون نسخه", tone: "warning" },
  stale: { en: "Stale", fa: "قدیمی", tone: "warning" },
  failing: { en: "Failing", fa: "در حال خطا", tone: "warning" },
  missing: { en: "Missing", fa: "یافت نشد", tone: "warning" },
  down: { en: "Down", fa: "از کار افتاده", tone: "danger" },
  unknown: { en: "Unknown", fa: "نامشخص", tone: "default" },
  never_run: { en: "Never run", fa: "هنوز اجرا نشده", tone: "default" },
  not_configured: { en: "Not configured", fa: "پیکربندی نشده", tone: "default" },
};

/**
 * P5: a status the table does not know is the raw string on the neutral
 * tone, never another label. `Object.hasOwn` keeps inherited keys such as
 * `constructor` or `toString` out of the lookup.
 */
export function presentStatus(status: string): StatusPresentation {
  return Object.hasOwn(STATUS_PRESENTATIONS, status)
    ? STATUS_PRESENTATIONS[status]
    : { en: status, fa: status, tone: "default" };
}

/** The runbook destinations P7 names. `sweeps` serves the card and each stale or failing task. */
export type RunbookSource =
  | "overall"
  | "database"
  | "redis"
  | "storage"
  | "schema"
  | "queue"
  | "sweeps"
  | "backup"
  | "audit";

export const RUNBOOK_BASE = "https://github.com/Kohandezh/Homayar.com/blob/main/docs/operations/";

const RUNBOOK_ANCHORS: Record<RunbookSource, string> = {
  overall: "health-and-recovery.md#first-response",
  database: "alerts.md#homayardatabaseunavailable",
  redis: "alerts.md#homayarredisunavailable",
  storage: "alerts.md#homayarobjectstorageunavailable",
  schema: "alerts.md#homayarschemabehindcode",
  queue: "health-and-recovery.md#worker-backlog-or-failure",
  sweeps: "alerts.md#homayarperiodicsweepstale",
  backup: "alerts.md#homayarbackuptooold",
  audit: "audit.md#delivery-and-failure-behaviour",
};

export function runbookHref(source: RunbookSource): string {
  return RUNBOOK_BASE + RUNBOOK_ANCHORS[source];
}

/** P7: only a warning or danger status gets a runbook link next to it. */
export function needsRunbook(tone: StatusTone): boolean {
  return tone === "warning" || tone === "danger";
}

/**
 * Whole seconds from `timestamp` to `referenceIso` (P6: the reference is the
 * response's checked_at, never the browser clock). Null when the timestamp
 * is null or unreadable; a timestamp in the future (clock skew) counts as 0.
 */
export function ageSecondsBetween(timestamp: string | null, referenceIso: string): number | null {
  if (!timestamp) return null;
  const at = Date.parse(timestamp);
  const reference = Date.parse(referenceIso);
  if (Number.isNaN(at) || Number.isNaN(reference)) return null;
  return Math.max(0, Math.floor((reference - at) / 1000));
}

type AgeUnit = "second" | "minute" | "hour" | "day";

function ageUnit(seconds: number): { value: number; unit: AgeUnit } {
  if (seconds < 60) return { value: seconds, unit: "second" };
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return { value: minutes, unit: "minute" };
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return { value: hours, unit: "hour" };
  return { value: Math.floor(hours / 24), unit: "day" };
}

const AGE_UNITS = {
  en: { second: "second", minute: "minute", hour: "hour", day: "day" },
  fa: { second: "ثانیه", minute: "دقیقه", hour: "ساعت", day: "روز" },
} as const;

/** An age as a duration with words ("5 minutes" / "۵ دقیقه"), so copy can add "ago". */
export function ageDurationText(seconds: number, lang: "fa" | "en"): string {
  const { value, unit } = ageUnit(Math.max(0, seconds));
  const number = formatNumber(value, lang);
  const word = AGE_UNITS[lang][unit];
  // English pluralizes ("1 minute", "5 minutes"); Persian never does.
  return lang === "fa" ? `${number} ${word}` : `${number} ${word}${value === 1 ? "" : "s"}`;
}
