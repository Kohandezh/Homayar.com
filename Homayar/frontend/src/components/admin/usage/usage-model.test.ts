import { describe, expect, it } from "vitest";
import { formatAudioHours } from "@/lib/format";
import {
  catalogMetrics,
  entitlementValue,
  eventTypeLabel,
  formatDateOnly,
  formatMetricValue,
  metricLabel,
  subjectLabel,
  usageLevel,
  usagePercent,
  usageRows,
} from "@/components/admin/usage/usage-model";
import type { BillingUsageSnapshot, JsonValue, Plan } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

function usage(metric: string, used: number, limit: number | null, reserved = 0): BillingUsageSnapshot {
  return {
    metric,
    used,
    reserved,
    limit,
    remaining: limit === null ? null : Math.max(0, limit - used - reserved),
    source: "plan",
    period_start: T,
    period_end: null,
    updated_at: T,
  };
}

function plan(key: string, entitlements: Record<string, JsonValue>): Plan {
  return {
    key,
    name: `Plan ${key}`,
    is_public: true,
    entitlements,
    price_published: false,
    pricing_mode: "manual_quote",
  };
}

describe("formatAudioHours (SPEC U2)", () => {
  it("matches every row of the U2 table in English", () => {
    expect(formatAudioHours(0, "en")).toBe("0 min");
    expect(formatAudioHours(23, "en")).toBe("< 1 min");
    expect(formatAudioHours(59.9, "en")).toBe("< 1 min");
    expect(formatAudioHours(1081, "en")).toBe("18 min");
    expect(formatAudioHours(3599, "en")).toBe("59 min");
    expect(formatAudioHours(3600, "en")).toBe("1 h");
    expect(formatAudioHours(3660, "en")).toBe("1 h 1 min");
    expect(formatAudioHours(7500, "en")).toBe("2 h 5 min");
    expect(formatAudioHours(36000, "en")).toBe("10 h");
    expect(formatAudioHours(-120, "en")).toBe("-2 min");
  });

  it("matches every row of the U2 table in Persian", () => {
    expect(formatAudioHours(0, "fa")).toBe("۰ دقیقه");
    expect(formatAudioHours(23, "fa")).toBe("کمتر از ۱ دقیقه");
    expect(formatAudioHours(59.9, "fa")).toBe("کمتر از ۱ دقیقه");
    expect(formatAudioHours(1081, "fa")).toBe("۱۸ دقیقه");
    expect(formatAudioHours(3599, "fa")).toBe("۵۹ دقیقه");
    expect(formatAudioHours(3600, "fa")).toBe("۱ ساعت");
    expect(formatAudioHours(3660, "fa")).toBe("۱ ساعت و ۱ دقیقه");
    expect(formatAudioHours(7500, "fa")).toBe("۲ ساعت و ۵ دقیقه");
    expect(formatAudioHours(36000, "fa")).toBe("۱۰ ساعت");
    expect(formatAudioHours(-120, "fa")).toBe("-۲ دقیقه");
  });

  it("never rounds up to a larger quota", () => {
    expect(formatAudioHours(1799, "en")).toBe("29 min");
    expect(formatAudioHours(1799, "fa")).toBe("۲۹ دقیقه");
  });

  it("prefixes a negative amount under a minute with the minus sign (Addendum B1)", () => {
    expect(formatAudioHours(-30, "en")).toBe("-< 1 min");
    expect(formatAudioHours(-30, "fa")).toBe("-کمتر از ۱ دقیقه");
  });

  it("shows audio as time and every other metric as a count", () => {
    expect(formatMetricValue("audio_seconds", 5430, "en")).toBe("1 h 30 min");
    expect(formatMetricValue("seats", 4, "en")).toBe("4");
    expect(formatMetricValue("seats", 4, "fa")).toBe("۴");
  });
});

describe("formatDateOnly (F7)", () => {
  it("shows a date without a time, in the reader's calendar", () => {
    const instant = "2026-09-24T12:00:00Z";
    const expected = new Intl.DateTimeFormat("en", { dateStyle: "medium" }).format(new Date(instant));
    expect(formatDateOnly(instant, "en")).toBe(expected);
    expect(formatDateOnly(instant, "en")).not.toMatch(/:/);
  });

  it("uses Persian digits and the Persian calendar in Persian", () => {
    const text = formatDateOnly("2026-09-24T12:00:00Z", "fa");
    expect(text).toMatch(/[\u06F0-\u06F9]/);
    expect(text).not.toMatch(/[0-9]/);
    expect(text).not.toMatch(/:/);
  });
});

describe("usagePercent", () => {
  it("floors the share and clamps it to 0..100", () => {
    expect(usagePercent(79, 100)).toBe(79);
    expect(usagePercent(79.6, 100)).toBe(79);
    expect(usagePercent(80, 100)).toBe(80);
    expect(usagePercent(99.9, 100)).toBe(99);
    expect(usagePercent(100, 100)).toBe(100);
    expect(usagePercent(130, 100)).toBe(100);
    expect(usagePercent(0, 100)).toBe(0);
  });

  it("counts a zero limit as full", () => {
    expect(usagePercent(0, 0)).toBe(100);
  });
});

describe("usageLevel (exact ratio, SPEC U4)", () => {
  it("warns from 80 percent and turns danger at the limit", () => {
    expect(usageLevel(79, 100)).toBe("normal");
    expect(usageLevel(79.6, 100)).toBe("normal");
    expect(usageLevel(79.9, 100)).toBe("normal");
    expect(usageLevel(80, 100)).toBe("warning");
    expect(usageLevel(99.9, 100)).toBe("warning");
    expect(usageLevel(99.99, 100)).toBe("warning");
    expect(usageLevel(100, 100)).toBe("danger");
    expect(usageLevel(130, 100)).toBe("danger");
  });

  it("counts a zero limit as reached", () => {
    expect(usageLevel(0, 0)).toBe("danger");
  });
});

describe("usageRows", () => {
  it("measures used plus reserved and keeps the known metrics first", () => {
    const rows = usageRows([
      usage("seats", 4, 5),
      usage("storage_bytes", 1, 10),
      usage("audio_seconds", 600, 1800, 90),
    ]);
    expect(rows.map((row) => row.metric)).toEqual(["audio_seconds", "seats", "storage_bytes"]);
    expect(rows[0]).toMatchObject({ consumed: 690, percent: 38, level: "normal", remaining: 1110 });
    expect(rows[1]).toMatchObject({ consumed: 4, percent: 80, level: "warning" });
  });

  it("marks a used-up quota danger and a zero limit full", () => {
    expect(usageRows([usage("audio_seconds", 1800, 1800)])[0]).toMatchObject({ percent: 100, level: "danger" });
    expect(usageRows([usage("concurrent_jobs", 0, 0)])[0]).toMatchObject({ percent: 100, level: "danger" });
  });

  it("leaves a quota without a limit at normal, without a percent", () => {
    expect(usageRows([usage("concurrent_jobs", 3, null)])[0]).toMatchObject({
      percent: null,
      level: "normal",
      remaining: null,
    });
  });
});

describe("labels", () => {
  it("names the known metrics, event types and subjects in both languages", () => {
    expect(metricLabel("audio_seconds", "en")).toBe("Audio processing");
    expect(metricLabel("audio_seconds", "fa")).toBe("پردازش صوت");
    expect(metricLabel("concurrent_jobs", "en")).toBe("Concurrent jobs");
    expect(metricLabel("concurrent_jobs", "fa")).toBe("پردازش‌های هم‌زمان");
    expect(metricLabel("seats", "en")).toBe("Seats");
    expect(metricLabel("seats", "fa")).toBe("ظرفیت اعضا");
    expect(eventTypeLabel("committed", "en")).toBe("Committed");
    expect(eventTypeLabel("committed", "fa")).toBe("ثبت قطعی");
    expect(eventTypeLabel("released", "fa")).toBe("آزادسازی");
    expect(subjectLabel("meeting", "en")).toBe("Meeting");
    expect(subjectLabel("meeting", "fa")).toBe("جلسه");
  });

  it("shows an unknown key as the server sent it, without reaching the prototype", () => {
    expect(metricLabel("storage_bytes", "en")).toBe("storage bytes");
    expect(metricLabel("constructor", "en")).toBe("constructor");
    expect(eventTypeLabel("custom_event", "en")).toBe("custom event");
    expect(subjectLabel("voice_room", "en")).toBe("voice room");
  });
});

describe("plan entitlements (SPEC U6)", () => {
  it("reads a limit, an explicit unlimited, and anything else as by agreement", () => {
    const demo = plan("demo", { audio_seconds: 1800, concurrent_jobs: null });
    expect(entitlementValue(demo, "audio_seconds")).toEqual({ kind: "limit", value: 1800 });
    expect(entitlementValue(demo, "concurrent_jobs")).toEqual({ kind: "unlimited" });
    expect(entitlementValue(demo, "seats")).toEqual({ kind: "by_agreement" });
    expect(entitlementValue(demo, "constructor")).toEqual({ kind: "by_agreement" });
    expect(entitlementValue(plan("odd", { seats: "many" }), "seats")).toEqual({ kind: "by_agreement" });
  });

  it("always lists the three known metrics, then any other key sorted", () => {
    const metrics = catalogMetrics([
      plan("a", { storage_bytes: 1, seats: 3 }),
      plan("b", { audio_seconds: 1800, zoo_quota: 2, alpha_quota: 3 }),
      plan("c", {}),
    ]);
    expect(metrics).toEqual(["audio_seconds", "concurrent_jobs", "seats", "alpha_quota", "storage_bytes", "zoo_quota"]);
  });
});
