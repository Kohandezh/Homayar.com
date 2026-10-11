import { NONTERMINAL_MEETING_STATUSES } from "@/lib/status-labels";
import type { MeetingStatus, UsageSnapshot } from "@/types/platform";

export interface MeetingCounts {
  completed: number;
  inProgress: number;
  failed: number;
  partial: number;
}

const IN_PROGRESS = new Set<MeetingStatus>(NONTERMINAL_MEETING_STATUSES);

/** Meetings per group. A cancelled meeting belongs to no group. */
export function countMeetings(items: readonly { status: MeetingStatus }[]): MeetingCounts {
  const counts: MeetingCounts = { completed: 0, inProgress: 0, failed: 0, partial: 0 };
  for (const { status } of items) {
    if (status === "completed") counts.completed += 1;
    else if (status === "failed") counts.failed += 1;
    else if (status === "partial") counts.partial += 1;
    else if (IN_PROGRESS.has(status)) counts.inProgress += 1;
  }
  return counts;
}

/** The quota metrics the overview shows, in display order. */
export const QUOTA_METRICS = ["audio_seconds", "concurrent_jobs", "seats"] as const;
export type QuotaMetric = (typeof QUOTA_METRICS)[number];

/** From this percent of the limit a quota row is near its limit. */
export const NEAR_LIMIT_PERCENT = 80;

export interface QuotaRow {
  metric: QuotaMetric;
  /** used + reserved */
  consumed: number;
  limit: number | null;
  /** Rounded and clamped to 0..100; null when there is no limit. */
  percent: number | null;
  nearLimit: boolean;
}

/** Share of the limit in whole percent (0..100). A zero limit counts as full. */
export function quotaPercent(consumed: number, limit: number): number {
  if (limit <= 0) return 100;
  return Math.min(100, Math.max(0, Math.round((100 * consumed) / limit)));
}

/**
 * One row per known metric, in QUOTA_METRICS order. Near limit uses the
 * rounded percent, so the text always matches the meter value.
 */
export function quotaRows(items: readonly UsageSnapshot[]): QuotaRow[] {
  return QUOTA_METRICS.flatMap((metric) => {
    const item = items.find((candidate) => candidate.metric === metric);
    if (!item) return [];
    const consumed = item.used + item.reserved;
    const percent = item.limit === null ? null : quotaPercent(consumed, item.limit);
    return [{ metric, consumed, limit: item.limit, percent, nearLimit: percent !== null && percent >= NEAR_LIMIT_PERCENT }];
  });
}

/** The readiness checks the chip names first, in this order. */
export const READINESS_CHECKS = ["database", "redis"] as const;
export type ReadinessCheck = (typeof READINESS_CHECKS)[number];

function isKnownCheck(key: string): key is ReadinessCheck {
  return (READINESS_CHECKS as readonly string[]).includes(key);
}

/**
 * The checks whose value is not "ready": database, then redis, then any
 * other check key in the order the server sent it.
 */
export function degradedChecks(checks: Readonly<Record<string, string>>): string[] {
  const known = READINESS_CHECKS.filter((check) => check in checks && checks[check] !== "ready");
  const others = Object.keys(checks).filter((key) => !isKnownCheck(key) && checks[key] !== "ready");
  return [...known, ...others];
}
