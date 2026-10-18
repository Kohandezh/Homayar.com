import { describe, expect, it } from "vitest";
import { CalendarDate, PersianCalendar, toCalendar } from "@internationalized/date";
import {
  dayEndIso,
  dayStartIso,
  isWindowInvalid,
  lastIncludedDay,
  lastIncludedIso,
  membershipPatch,
  orgMemberStatus,
  orgTotals,
  sinceFor,
  statusCount,
} from "@/components/org/org-model";
import { aggregate, member } from "@/components/org/org-test-kit";

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
  it("sums over the items, and counts a missing status as 0", () => {
    const items = [aggregate(), aggregate({ workspace_id: "ws-2", meetings_total: 5, audio_seconds_total: 60, action_items_total: 1, meetings_by_status: {} })];
    expect(orgTotals(items)).toEqual({ workspaces: 2, meetings: 35, audioSeconds: 1225, actionItems: 28 });
    expect(orgTotals([])).toEqual({ workspaces: 0, meetings: 0, audioSeconds: 0, actionItems: 0 });
    expect(statusCount(items[0], "completed")).toBe(25);
    expect(statusCount(items[1], "completed")).toBe(0);
    expect(statusCount(items[1], "constructor")).toBe(0);
  });

  it("computes since as now minus N days, and none for all time", () => {
    const now = Date.parse("2026-10-18T12:00:00Z");
    expect(sinceFor("all", now)).toBeNull();
    expect(sinceFor("7", now)).toBe("2026-10-11T12:00:00.000Z");
    expect(sinceFor("90", now)).toBe("2026-07-20T12:00:00.000Z");
  });
});
