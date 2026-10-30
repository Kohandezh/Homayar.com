/**
 * The issue trends page's filters (HOM-237): how they are read from and
 * written to the URL, and how they become the query of GET /trends/issues.
 * The filters live in the URL so a reload keeps them.
 *
 * Days are Gregorian "YYYY-MM-DD" and both ends are inclusive. They count in
 * Tehran, the zone of the series: the API releases only closed Tehran weeks
 * (Saturday to Friday), so both presets end on the Friday before this week.
 */
import { fromAbsolute, getDayOfWeek, toCalendarDate, type CalendarDate } from "@internationalized/date";

import { parseAuditDay } from "@/lib/audit-filters";
import type { IssueTrendParams } from "@/types/trends";

export const TREND_TIMEZONE = "Asia/Tehran";

export type TrendFilterGrain = "week" | "month";
export type TrendPreset = "4w" | "12w";

export const TREND_PRESETS: readonly TrendPreset[] = ["4w", "12w"];
const PRESET_WEEKS: Record<TrendPreset, number> = { "4w": 4, "12w": 12 };

export interface TrendFilters {
  /** First day, inclusive. */
  from: string;
  /** Last day, inclusive. */
  to: string;
  /** One team; empty means every team. */
  teamId: string;
  grain: TrendFilterGrain;
}

/** The Saturday that starts the current week in Tehran. */
export function tehranWeekStart(now: Date): CalendarDate {
  const today = toCalendarDate(fromAbsolute(now.getTime(), TREND_TIMEZONE));
  // "en-US" weeks start on Sunday (0), so Saturday is 6.
  const sinceSaturday = (getDayOfWeek(today, "en-US") + 1) % 7;
  return today.subtract({ days: sinceSaturday });
}

/** The days of a preset: whole weeks that end on the day before the current week. */
export function trendPresetFilters(preset: TrendPreset, now: Date): Pick<TrendFilters, "from" | "to"> {
  const weekStart = tehranWeekStart(now);
  return {
    from: weekStart.subtract({ weeks: PRESET_WEEKS[preset] }).toString(),
    to: weekStart.subtract({ days: 1 }).toString(),
  };
}

/** The preset whose days the filters hold; null for a custom range. */
export function activeTrendPreset(filters: TrendFilters, now: Date): TrendPreset | null {
  return (
    TREND_PRESETS.find((preset) => {
      const range = trendPresetFilters(preset, now);
      return range.from === filters.from && range.to === filters.to;
    }) ?? null
  );
}

/** The filters a URL holds; a missing, half or inverted range is the last 4 weeks. */
export function readTrendFilters(search: URLSearchParams, now: Date): TrendFilters {
  const fromText = search.get("from")?.trim() ?? "";
  const toText = search.get("to")?.trim() ?? "";
  const from = parseAuditDay(fromText);
  const to = parseAuditDay(toText);
  const range =
    from && to && from.compare(to) <= 0 ? { from: fromText, to: toText } : trendPresetFilters("4w", now);
  const grain = search.get("grain");
  return {
    ...range,
    teamId: search.get("team_id")?.trim() ?? "",
    grain: grain === "month" ? "month" : "week",
  };
}

/** The filters as URL query parameters; an empty team is removed and any other parameter in `base` kept. */
export function writeTrendFilters(filters: TrendFilters, base?: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams(base);
  next.set("from", filters.from);
  next.set("to", filters.to);
  if (filters.teamId) next.set("team_id", filters.teamId);
  else next.delete("team_id");
  next.set("grain", filters.grain);
  return next;
}

/**
 * The request: `from` is the first day's midnight in Tehran and `to`, which
 * the API reads as exclusive, the midnight after the last day.
 */
export function trendQueryParams(filters: TrendFilters): IssueTrendParams {
  const from = parseAuditDay(filters.from);
  const to = parseAuditDay(filters.to);
  if (!from || !to) throw new Error("trendQueryParams needs the days readTrendFilters returns");
  const params: IssueTrendParams = {
    from: from.toDate(TREND_TIMEZONE).toISOString(),
    to: to.add({ days: 1 }).toDate(TREND_TIMEZONE).toISOString(),
    grain: filters.grain,
    timezone: TREND_TIMEZONE,
  };
  if (filters.teamId) params.team_id = filters.teamId;
  return params;
}
