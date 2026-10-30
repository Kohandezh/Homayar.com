import { formatNumber } from "@/lib/format";
import type { Lang } from "@/lib/i18n-context";
import { trendFamilyLabel, trendIssueLabel } from "@/lib/trend-labels";
import type { TrendPoint, TrendRow } from "@/types/trends";
import { TRENDS_COPY } from "@/components/trends/trends-copy";

/** `{numerator} of {denominator}` with the language's digits. */
export function ofTotal(numerator: number, denominator: number, lang: Lang): string {
  return TRENDS_COPY[lang].ofTotal(formatNumber(numerator, lang), formatNumber(denominator, lang));
}

/** The API's ratio as a whole percent; never recomputed from the counts. */
export function ratioPercent(ratio: number, lang: Lang): string {
  return `${formatNumber(Math.round(ratio * 100), lang)}${lang === "fa" ? "٪" : "%"}`;
}

/**
 * A computed value as one text, `7 of 20 · 40%`: the separator keeps the
 * denominator and the percent apart when the text is copied or read aloud.
 */
export function computedValue(numerator: number, denominator: number, ratio: number, lang: Lang): string {
  return `${ofTotal(numerator, denominator, lang)} · ${ratioPercent(ratio, lang)}`;
}

/**
 * A period's label: its first day in the series timezone (not the reader's),
 * so a Tehran Saturday never reads as Friday. Persian uses the Persian calendar.
 */
export function periodLabel(point: TrendPoint, timeZone: string, lang: Lang): string {
  return new Intl.DateTimeFormat(lang === "fa" ? "fa-IR" : "en", { dateStyle: "medium", timeZone }).format(
    new Date(point.from),
  );
}

/** The API's name of a row: `family:<id>` or `issue:<id>`. */
export function trendRowKey(row: Pick<TrendRow, "level" | "family" | "issue">): string {
  return row.level === "family" ? `family:${row.family}` : `issue:${row.issue ?? ""}`;
}

export function trendRowName(row: Pick<TrendRow, "level" | "family" | "issue">, lang: Lang): string {
  return row.level === "family" || row.issue === null
    ? trendFamilyLabel(row.family, lang)
    : trendIssueLabel(row.issue, lang);
}
