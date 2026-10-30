import type { Lang } from "@/lib/i18n-context";
import type { MissingDataIndicator } from "@/types/organizations";

/**
 * Every issue trends page text, in the console's two languages (HOM-237).
 * Numbers arrive already formatted for the language. A code the tables do
 * not know is shown raw, never guessed.
 */
export const TRENDS_COPY = {
  en: {
    eyebrow: "Workspace",
    title: "Issue trends",
    intro:
      "Top call reasons, unresolved outcomes and product or service complaints over time. Every number shows the calls it is counted from.",

    presetsLabel: "Date range presets",
    presets: { "4w": "Last 4 weeks", "12w": "Last 12 weeks" },
    dateRange: "Date range",
    chooseDateRange: "Choose a date range",
    team: "Team",
    allTeams: "All teams",
    grainLabel: "Period",
    grains: { week: "Week", month: "Month" },

    loading: "Loading issue trends",
    loadFailed: "Could not load the issue trends.",
    retry: "Try again",
    empty: "No trend data for this range.",

    processingCoverage: "Processing coverage",
    evaluationCoverage: "Evaluation coverage",
    coverageLabel: "Coverage",
    callsCounted: (included: string) => `${included} calls counted`,

    ofTotal: (numerator: string, denominator: string) => `${numerator} of ${denominator}`,
    notAvailable: "Not available",
    tooFewCalls: "Too few calls",
    sampleNeeded: (denominator: string, minimum: string) => `${denominator} of ${minimum} needed`,
    withheld: "Withheld to protect a small group",
    calls: (denominator: string) => `${denominator} calls`,
    waitingForReview: (count: string) => `${count} waiting for review`,
    reasons: {
      empty_denominator: "No calls in this period",
      source_unavailable: "No source data yet",
    },

    tableLabel: "Issue trends",
    columnName: "Issue",
    columnWindow: "Whole range",
    columnTrend: "Trend",
    partial: "partial",
    trendOf: (name: string) => `Trend: ${name}`,
    showCalls: (name: string, period: string) => `Show calls: ${name}, ${period}`,
    wholeRange: "whole range",
    callsButton: "Calls",

    maskedPoints: "Per-period numbers are withheld to protect a small group.",
    maskedWindow: "Totals for the whole range are withheld to protect a small group.",
    correlationNotCause: "These counts show what happened in calls. They do not show a cause.",
    restrictedScope: "You see only the calls in your record scope.",
    dataGaps: "Data gaps",

    supportingCalls: "Supporting calls",
    closeCalls: "Close supporting calls",
    loadingCalls: "Loading calls",
    callsUnavailable: "These calls are not available.",
    noCalls: "No calls to show.",
    openCall: (position: string) => `Open call ${position}`,
    evidence: (count: string) => `Evidence: ${count}`,
    callsPaging: "Supporting calls pagination",
    previous: "Previous",
    next: "Next",
  },
  fa: {
    eyebrow: "فضای کاری",
    title: "روند مسائل",
    intro:
      "دلایل اصلی تماس، نتیجه‌های حل‌نشده و شکایت از محصول یا خدمت در طول زمان. هر عدد نشان می‌دهد از چند تماس شمرده شده است.",

    presetsLabel: "بازه‌های آماده",
    presets: { "4w": "۴ هفتهٔ گذشته", "12w": "۱۲ هفتهٔ گذشته" },
    dateRange: "بازهٔ زمانی",
    chooseDateRange: "انتخاب بازهٔ زمانی",
    team: "تیم",
    allTeams: "همهٔ تیم‌ها",
    grainLabel: "دوره",
    grains: { week: "هفته", month: "ماه" },

    loading: "در حال دریافت روند مسائل",
    loadFailed: "دریافت روند مسائل ناموفق بود.",
    retry: "تلاش دوباره",
    empty: "برای این بازه داده‌ای نیست.",

    processingCoverage: "پوشش پردازش",
    evaluationCoverage: "پوشش ارزیابی",
    coverageLabel: "پوشش",
    callsCounted: (included: string) => `${included} تماس شمرده شد`,

    ofTotal: (numerator: string, denominator: string) => `${numerator} از ${denominator}`,
    notAvailable: "در دسترس نیست",
    tooFewCalls: "تماس کافی نیست",
    sampleNeeded: (denominator: string, minimum: string) => `${denominator} از ${minimum} لازم`,
    withheld: "برای حفاظت از گروه کوچک پنهان شده",
    calls: (denominator: string) => `${denominator} تماس`,
    waitingForReview: (count: string) => `${count} در انتظار بازبینی`,
    reasons: {
      empty_denominator: "در این بازه تماسی نیست",
      source_unavailable: "هنوز داده‌ای نیست",
    },

    tableLabel: "روند مسائل",
    columnName: "مسئله",
    columnWindow: "کل بازه",
    columnTrend: "روند",
    partial: "ناقص",
    trendOf: (name: string) => `روند: ${name}`,
    showCalls: (name: string, period: string) => `نمایش تماس‌ها: ${name}، ${period}`,
    wholeRange: "کل بازه",
    callsButton: "تماس‌ها",

    maskedPoints: "عددهای هر بازه برای حفاظت از گروه کوچک پنهان شده است.",
    maskedWindow: "جمع کل بازه برای حفاظت از گروه کوچک پنهان شده است.",
    correlationNotCause: "این شمارش‌ها نشان می‌دهد در تماس‌ها چه گذشت، نه علت آن را.",
    restrictedScope: "فقط تماس‌های محدودهٔ دسترسی شما نشان داده می‌شود.",
    dataGaps: "کمبود داده",

    supportingCalls: "تماس‌های پشتیبان",
    closeCalls: "بستن تماس‌های پشتیبان",
    loadingCalls: "در حال دریافت تماس‌ها",
    callsUnavailable: "این تماس‌ها در دسترس نیستند.",
    noCalls: "تماسی برای نمایش نیست.",
    openCall: (position: string) => `باز کردن تماس ${position}`,
    evidence: (count: string) => `شواهد: ${count}`,
    callsPaging: "صفحه‌بندی تماس‌های پشتیبان",
    previous: "قبلی",
    next: "بعدی",
  },
} as const;

export type TrendsCopy = (typeof TRENDS_COPY)["en" | "fa"];

/** The reasons a family gives for a source it cannot read yet (trends.v1.json `source_reason`). */
const SOURCE_REASONS: ReadonlySet<string> = new Set(["scorecard_results_unavailable", "source_unavailable"]);

/** The text of a `not_computable` cell's reason; an unknown reason is the raw code. */
export function trendReasonLabel(reason: string | null, lang: Lang): string | null {
  if (!reason) return null;
  const copy = TRENDS_COPY[lang];
  if (reason === "empty_denominator") return copy.reasons.empty_denominator;
  if (SOURCE_REASONS.has(reason)) return copy.reasons.source_unavailable;
  return reason;
}

const DATA_GAP_LABELS: Record<MissingDataIndicator, { en: string; fa: string }> = {
  ingestion_incomplete: { en: "Some calls are still being received", fa: "دریافت برخی تماس‌ها هنوز تمام نشده است" },
  processing_incomplete: { en: "Some calls are still being processed", fa: "پردازش برخی تماس‌ها هنوز تمام نشده است" },
  evaluation_incomplete: { en: "Some calls are not evaluated yet", fa: "ارزیابی برخی تماس‌ها هنوز انجام نشده است" },
  review_incomplete: { en: "Some reviews are not finished", fa: "برخی بازبینی‌ها هنوز تمام نشده است" },
  ingestion_unavailable: { en: "Ingestion data is not available", fa: "دادهٔ دریافت در دسترس نیست" },
  processing_unavailable: { en: "Processing data is not available", fa: "دادهٔ پردازش در دسترس نیست" },
  evaluation_unavailable: { en: "Evaluation data is not available", fa: "دادهٔ ارزیابی در دسترس نیست" },
  review_unavailable: { en: "Review data is not available", fa: "دادهٔ بازبینی در دسترس نیست" },
  scorecard_unavailable: { en: "No scorecard results yet", fa: "هنوز نتیجهٔ کارت امتیازدهی نیست" },
  empty_population: { en: "No calls in this range", fa: "در این بازه تماسی نیست" },
  missing_values: { en: "Some values are missing", fa: "برخی مقدارها موجود نیست" },
  pending_review: { en: "Some calls are waiting for review", fa: "برخی تماس‌ها در انتظار بازبینی است" },
  insufficient_sample: { en: "Some numbers have too few calls", fa: "برخی عددها تماس کافی ندارند" },
  source_unavailable: { en: "A data source is not available", fa: "یک منبع داده در دسترس نیست" },
};

/** A data gap indicator's label; an unknown code is shown as sent. */
export function dataGapLabel(code: string, lang: Lang): string {
  return Object.hasOwn(DATA_GAP_LABELS, code) ? DATA_GAP_LABELS[code as MissingDataIndicator][lang] : code;
}
