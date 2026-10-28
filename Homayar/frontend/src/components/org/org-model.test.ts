import { afterEach, describe, expect, it, vi } from "vitest";
import { CalendarDate, PersianCalendar, resetLocalTimeZone, toCalendar } from "@internationalized/date";
import {
  dayEndIso,
  dayStartIso,
  isWindowInvalid,
  lastIncludedDay,
  lastIncludedIso,
  membershipPatch,
  isReleased,
  orgMemberStatus,
  orgTotals,
  sinceForWeeks,
  statusCount,
  windowDays,
} from "@/components/org/org-model";
import { DEFAULT_WINDOW, aggregate, member, withheld } from "@/components/org/org-test-kit";

const NOW = new Date("2026-10-18T12:00:00Z");

describe("orgMemberStatus", () => {
  it("decides in the SPEC order: revoked, inactive, active, scheduled, expired", () => {
    expect(orgMemberStatus(member({ revoked_at: "2026-01-01T00:00:00Z", is_active: false }), NOW)).toBe("revoked");
    expect(orgMemberStatus(member({ is_active: false }), NOW)).toBe("inactive");
    expect(orgMemberStatus(member({ is_effective: true }), NOW)).toBe("active");
    expect(orgMemberStatus(member({ is_effective: false, valid_from: "2026-11-01T00:00:00Z" }), NOW)).toBe("scheduled");
    expect(orgMemberStatus(member({ is_effective: false, valid_until: "2026-02-01T00:00:00Z" }), NOW)).toBe("expired");
  });
});

describe("dates", () => {
  it("sends a start day as the midnight that starts it, and an end day as the next midnight", () => {
    expect(dayStartIso(new CalendarDate(2026, 11, 1))).toBe(new Date(2026, 10, 1).toISOString());
    // The picked end day is included: access ends when the next day starts.
    expect(dayEndIso(new CalendarDate(2026, 11, 30))).toBe(new Date(2026, 11, 1).toISOString());
    expect(dayEndIso(new CalendarDate(2026, 12, 31))).toBe(new Date(2027, 0, 1).toISOString());
  });

  it("reads back the last included day, so the round trip keeps the picked day", () => {
    const end = dayEndIso(new CalendarDate(2027, 3, 1));
    expect(lastIncludedDay(end)?.toString()).toBe("2027-03-01");
    expect(lastIncludedIso(end)).toBe(new Date(new Date(2027, 2, 2).getTime() - 1).toISOString());
    expect(lastIncludedDay(null)).toBeNull();
  });

  it("refuses only an end day before the start day; the same day is one day of access", () => {
    const day = (date: number) => new CalendarDate(2026, 12, date);
    expect(isWindowInvalid(day(2), day(1))).toBe(true);
    expect(isWindowInvalid(day(1), day(1))).toBe(false);
    expect(isWindowInvalid(day(1), day(2))).toBe(false);
    expect(isWindowInvalid(null, day(1))).toBe(false);
    expect(isWindowInvalid(day(1), null)).toBe(false);
  });

  it("compares days of different calendars by the day they name", () => {
    const persian = toCalendar(new CalendarDate(2026, 11, 1), new PersianCalendar());
    expect(isWindowInvalid(persian, new CalendarDate(2026, 10, 31))).toBe(true);
    expect(isWindowInvalid(persian, new CalendarDate(2026, 11, 1))).toBe(false);
    expect(dayEndIso(persian)).toBe(new Date(2026, 10, 2).toISOString());
  });
});

describe("membershipPatch", () => {
  const open = { role: "analyst", valid_until: null };
  // Stored end: the midnight that starts 2027-03-02, so the last included day is 2027-03-01.
  const ending = { role: "analyst", valid_until: new Date(2027, 2, 2).toISOString() };
  const march1 = new CalendarDate(2027, 3, 1);

  it("is empty without a change", () => {
    expect(membershipPatch(open, { role: "analyst", noEndDate: true, untilDay: null })).toEqual({});
    expect(membershipPatch(open, { role: "analyst", noEndDate: false, untilDay: null })).toEqual({});
    expect(membershipPatch(ending, { role: "analyst", noEndDate: false, untilDay: march1 })).toEqual({});
    // The same day picked in the Persian calendar is no change either.
    const persian = toCalendar(march1, new PersianCalendar());
    expect(membershipPatch(ending, { role: "analyst", noEndDate: false, untilDay: persian })).toEqual({});
  });

  it("holds only the changed role", () => {
    expect(membershipPatch(open, { role: "executive", noEndDate: true, untilDay: null })).toEqual({ role: "executive" });
  });

  it("holds a new end date as the next midnight, or clear_valid_until when No end date is ticked", () => {
    expect(membershipPatch(open, { role: "analyst", noEndDate: false, untilDay: march1 })).toEqual({
      valid_until: new Date(2027, 2, 2).toISOString(),
    });
    expect(membershipPatch(ending, { role: "analyst", noEndDate: true, untilDay: null })).toEqual({ clear_valid_until: true });
  });

  it("never clears the end date while No end date is unticked, even with an empty date", () => {
    expect(membershipPatch(ending, { role: "analyst", noEndDate: false, untilDay: null })).toEqual({});
    expect(membershipPatch(ending, { role: "executive", noEndDate: false, untilDay: null })).toEqual({ role: "executive" });
  });
});

describe("totals", () => {
  const released = [
    aggregate(),
    aggregate({ workspace_id: "ws-2", meetings_total: 5, audio_seconds_total: 60, action_items_total: 1, meetings_by_status: {} }),
  ].filter(isReleased);

  it("sums over released items, and counts a missing status as 0", () => {
    expect(orgTotals(released)).toEqual({ workspaces: 2, hidden: 0, meetings: 35, audioSeconds: 1225, actionItems: 28 });
    expect(orgTotals([])).toEqual({ workspaces: 0, hidden: 0, meetings: 0, audioSeconds: 0, actionItems: 0 });
    expect(statusCount(released[0], "completed")).toBe(25);
    expect(statusCount(released[1], "completed")).toBe(0);
    expect(statusCount(released[1], "constructor")).toBe(0);
  });

  it("counts a hidden item as a workspace but never adds its counts", () => {
    const items = [...released, withheld("small_group"), withheld("complementary_disclosure")];
    expect(orgTotals(items)).toEqual({ workspaces: 4, hidden: 2, meetings: 35, audioSeconds: 1225, actionItems: 28 });
  });

  it("releases an item only when the status is released and every count is a number", () => {
    expect(isReleased(aggregate())).toBe(true);
    expect(isReleased(aggregate({ meetings_total: 0, meetings_by_status: {}, audio_seconds_total: 0, action_items_total: 0 }))).toBe(true);
    expect(isReleased(withheld("small_group"))).toBe(false);
    expect(isReleased(withheld(null))).toBe(false);
    expect(isReleased(aggregate({ disclosure: { status: "masked", reason: null, kind: null, masked: ["points"] } }))).toBe(false);
    expect(isReleased(aggregate({ disclosure: { status: "withheld", reason: "small_group", kind: null, masked: [] } }))).toBe(false);
    expect(isReleased(aggregate({ meetings_total: null }))).toBe(false);
    expect(isReleased(aggregate({ meetings_by_status: null }))).toBe(false);
    expect(isReleased(aggregate({ audio_seconds_total: null }))).toBe(false);
    expect(isReleased(aggregate({ action_items_total: null }))).toBe(false);
  });
});

describe("closed weeks", () => {
  const now = Date.parse("2026-10-28T10:00:00.000Z");
  afterEach(() => {
    vi.unstubAllEnvs();
    resetLocalTimeZone();
  });

  it("sends nothing for the default 4 weeks, and N weeks before this Saturday 00:00 Tehran otherwise", () => {
    expect(sinceForWeeks("4", now)).toBeNull();
    expect(sinceForWeeks("13", now)).toBe("2026-07-24T20:30:00.000Z");
    expect(sinceForWeeks("26", now)).toBe("2026-04-24T20:30:00.000Z");
    expect(sinceForWeeks("52", now)).toBe("2025-10-24T20:30:00.000Z");
  });

  it("starts the week on Saturday 00:00 Tehran, at the edge too", () => {
    // Saturday 2026-10-24 00:00 Tehran is still in the new week; one millisecond before is the old one.
    expect(sinceForWeeks("13", Date.parse("2026-10-23T20:30:00.000Z"))).toBe("2026-07-24T20:30:00.000Z");
    expect(sinceForWeeks("13", Date.parse("2026-10-23T20:29:59.999Z"))).toBe("2026-07-17T20:30:00.000Z");
  });

  it("gives the same instant whatever the browser's time zone", () => {
    for (const tz of ["UTC", "America/New_York", "Asia/Tokyo"]) {
      // The library caches the browser's zone; forget it, like a browser that started in `tz`.
      vi.stubEnv("TZ", tz);
      resetLocalTimeZone();
      expect(sinceForWeeks("13", now)).toBe("2026-07-24T20:30:00.000Z");
      expect(windowDays(DEFAULT_WINDOW, "en")).toEqual({ from: "Sep 26, 2026", last: "Oct 23, 2026" });
    }
  });

  it("shows the window as its first day and its last included day, Tehran time", () => {
    expect(windowDays(DEFAULT_WINDOW, "en")).toEqual({ from: "Sep 26, 2026", last: "Oct 23, 2026" });
    expect(windowDays(DEFAULT_WINDOW, "fa")).toEqual({ from: "۴ مهر ۱۴۰۵", last: "۱ آبان ۱۴۰۵" });
  });
});
