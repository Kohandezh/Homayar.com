import { describe, expect, it } from "vitest";

import {
  RUNBOOK_BASE,
  ageDurationText,
  ageSecondsBetween,
  presentStatus,
  runbookHref,
} from "@/components/admin/system-health/system-health-model";

/** HOM-232 P4: the label and tone of every status value the API can send. */
describe("presentStatus", () => {
  it.each([
    ["ok", "OK", "سالم", "success"],
    ["up", "Up", "در دسترس", "success"],
    ["up_to_date", "Up to date", "به‌روز", "success"],
    ["degraded", "Degraded", "دچار اختلال", "warning"],
    ["timeout", "Timed out", "بدون پاسخ", "warning"],
    ["behind", "Behind", "عقب‌تر از کد", "warning"],
    ["unstamped", "Unstamped", "بدون نسخه", "warning"],
    ["stale", "Stale", "قدیمی", "warning"],
    ["failing", "Failing", "در حال خطا", "warning"],
    ["missing", "Missing", "یافت نشد", "warning"],
    ["down", "Down", "از کار افتاده", "danger"],
    ["unknown", "Unknown", "نامشخص", "default"],
    ["never_run", "Never run", "هنوز اجرا نشده", "default"],
    ["not_configured", "Not configured", "پیکربندی نشده", "default"],
  ] as const)("maps %s to %s / %s on tone %s", (value, en, fa, tone) => {
    expect(presentStatus(value)).toEqual({ en, fa, tone });
  });

  it("shows an unknown status string as itself, left to right, on the neutral tone", () => {
    expect(presentStatus("rebuilding")).toEqual({ en: "rebuilding", fa: "rebuilding", tone: "default" });
  });

  it("treats inherited object keys (constructor, __proto__) as unknown statuses", () => {
    for (const value of ["constructor", "toString", "__proto__"]) {
      expect(presentStatus(value)).toEqual({ en: value, fa: value, tone: "default" });
    }
  });
});

/** HOM-232 P7: every warning or danger source opens its own runbook anchor. */
describe("runbookHref", () => {
  it.each([
    ["database", "alerts.md#homayardatabaseunavailable"],
    ["redis", "alerts.md#homayarredisunavailable"],
    ["storage", "alerts.md#homayarobjectstorageunavailable"],
    ["schema", "alerts.md#homayarschemabehindcode"],
    ["queue", "health-and-recovery.md#worker-backlog-or-failure"],
    ["sweeps", "alerts.md#homayarperiodicsweepstale"],
    ["backup", "alerts.md#homayarbackuptooold"],
    ["audit", "audit.md#delivery-and-failure-behaviour"],
    ["overall", "health-and-recovery.md#first-response"],
  ] as const)("maps %s to %s under the operations base", (source, anchor) => {
    expect(runbookHref(source)).toBe(`${RUNBOOK_BASE}${anchor}`);
  });

  it("uses the Homayar docs base URL", () => {
    expect(RUNBOOK_BASE).toBe("https://github.com/Kohandezh/Homayar.com/blob/main/docs/operations/");
  });
});

/** HOM-232 P6: ages count from the response's checked_at, not the browser clock. */
describe("ageSecondsBetween", () => {
  const REFERENCE = "2026-10-23T12:00:00Z";

  it("is null when the timestamp is null or cannot be read", () => {
    expect(ageSecondsBetween(null, REFERENCE)).toBeNull();
    expect(ageSecondsBetween("not a date", REFERENCE)).toBeNull();
  });

  it("counts whole seconds from the timestamp to the reference", () => {
    expect(ageSecondsBetween("2026-10-23T11:55:00Z", REFERENCE)).toBe(300);
    expect(ageSecondsBetween("2026-10-23T11:59:59.4Z", REFERENCE)).toBe(0);
  });

  it("counts a timestamp in the future (clock skew) as zero, never negative", () => {
    expect(ageSecondsBetween("2026-10-23T12:01:00Z", REFERENCE)).toBe(0);
  });
});

/** HOM-232 P6: an age as a duration with words, so it reads next to "ago". */
describe("ageDurationText", () => {
  it.each([
    [0, "0 seconds", "۰ ثانیه"],
    [1, "1 second", "۱ ثانیه"],
    [30, "30 seconds", "۳۰ ثانیه"],
    [59, "59 seconds", "۵۹ ثانیه"],
    [60, "1 minute", "۱ دقیقه"],
    [300, "5 minutes", "۵ دقیقه"],
    [3599, "59 minutes", "۵۹ دقیقه"],
    [3600, "1 hour", "۱ ساعت"],
    [7200, "2 hours", "۲ ساعت"],
    [86_400, "1 day", "۱ روز"],
    [86_400 * 3, "3 days", "۳ روز"],
  ])("%i s reads as %s (en) and %s (fa)", (seconds, en, fa) => {
    expect(ageDurationText(seconds, "en")).toBe(en);
    expect(ageDurationText(seconds, "fa")).toBe(fa);
  });
});
