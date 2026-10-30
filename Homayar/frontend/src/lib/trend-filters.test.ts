import { describe, expect, it } from "vitest";
import {
  activeTrendPreset,
  readTrendFilters,
  tehranWeekStart,
  trendPresetFilters,
  trendQueryParams,
  writeTrendFilters,
} from "@/lib/trend-filters";

// Friday 2026-10-30, 12:00 in Tehran (UTC+03:30): the week began on Saturday 2026-10-24.
const FRIDAY = new Date("2026-10-30T08:30:00Z");

describe("tehranWeekStart", () => {
  it("is the Saturday of the current Tehran week", () => {
    expect(tehranWeekStart(FRIDAY).toString()).toBe("2026-10-24");
  });

  it("counts a Saturday as the first day of its own week", () => {
    // Saturday 2026-10-31, 00:10 in Tehran, still Friday in UTC.
    expect(tehranWeekStart(new Date("2026-10-30T20:40:00Z")).toString()).toBe("2026-10-31");
  });

  it("reads the day in Tehran, not in UTC", () => {
    // Friday 23:50 UTC is already Saturday 03:20 in Tehran.
    expect(tehranWeekStart(new Date("2026-10-30T23:50:00Z")).toString()).toBe("2026-10-31");
    // Saturday 00:10 UTC is Saturday 03:40 in Tehran, the same week.
    expect(tehranWeekStart(new Date("2026-10-31T00:10:00Z")).toString()).toBe("2026-10-31");
  });
});

describe("trend presets", () => {
  it("ends both presets on the day before the week start", () => {
    expect(trendPresetFilters("4w", FRIDAY)).toEqual({ from: "2026-09-26", to: "2026-10-23" });
    expect(trendPresetFilters("12w", FRIDAY)).toEqual({ from: "2026-08-01", to: "2026-10-23" });
  });

  it("names the preset a range matches, and none for a custom range", () => {
    const base = { teamId: "", grain: "week" as const };
    expect(activeTrendPreset({ ...base, ...trendPresetFilters("4w", FRIDAY) }, FRIDAY)).toBe("4w");
    expect(activeTrendPreset({ ...base, ...trendPresetFilters("12w", FRIDAY) }, FRIDAY)).toBe("12w");
    expect(activeTrendPreset({ ...base, from: "2026-10-01", to: "2026-10-10" }, FRIDAY)).toBeNull();
  });

  it("presses no preset for a custom range read from the URL, and 4 weeks for no range", () => {
    const custom = readTrendFilters(new URLSearchParams("from=2026-10-01&to=2026-10-10"), FRIDAY);
    expect(activeTrendPreset(custom, FRIDAY)).toBeNull();
    // A custom range that is one day off a preset is still custom.
    const offByOne = readTrendFilters(new URLSearchParams("from=2026-09-26&to=2026-10-24"), FRIDAY);
    expect(activeTrendPreset(offByOne, FRIDAY)).toBeNull();
    expect(activeTrendPreset(readTrendFilters(new URLSearchParams(), FRIDAY), FRIDAY)).toBe("4w");
  });
});

describe("readTrendFilters", () => {
  it("defaults to the last 4 weeks by week for all teams", () => {
    expect(readTrendFilters(new URLSearchParams(), FRIDAY)).toEqual({
      from: "2026-09-26",
      to: "2026-10-23",
      teamId: "",
      grain: "week",
    });
  });

  it("keeps a valid range, team and grain from the URL", () => {
    const search = new URLSearchParams("from=2026-10-01&to=2026-10-10&team_id=team-7&grain=month");
    expect(readTrendFilters(search, FRIDAY)).toEqual({
      from: "2026-10-01",
      to: "2026-10-10",
      teamId: "team-7",
      grain: "month",
    });
  });

  it("falls back to the default range for a half, malformed or inverted range", () => {
    for (const query of ["from=2026-10-01", "to=2026-10-10", "from=yesterday&to=2026-10-10", "from=2026-10-10&to=2026-10-01", "from=2026-02-30&to=2026-03-02"]) {
      const filters = readTrendFilters(new URLSearchParams(query), FRIDAY);
      expect([filters.from, filters.to], query).toEqual(["2026-09-26", "2026-10-23"]);
    }
  });

  it("falls back to week for any other grain", () => {
    for (const grain of ["day", "none", "year", ""]) {
      expect(readTrendFilters(new URLSearchParams({ grain }), FRIDAY).grain).toBe("week");
    }
  });
});

describe("writeTrendFilters", () => {
  it("writes every filter and drops an empty team", () => {
    const written = writeTrendFilters(
      { from: "2026-10-01", to: "2026-10-10", teamId: "", grain: "month" },
      new URLSearchParams("team_id=old&keep=1"),
    );
    expect(Object.fromEntries(written)).toEqual({
      from: "2026-10-01",
      to: "2026-10-10",
      grain: "month",
      keep: "1",
    });
  });

  it("round-trips through readTrendFilters", () => {
    const filters = { from: "2026-10-01", to: "2026-10-10", teamId: "team-7", grain: "week" as const };
    expect(readTrendFilters(writeTrendFilters(filters), FRIDAY)).toEqual(filters);
  });
});

describe("trendQueryParams", () => {
  it("sends the default as 4 closed Tehran weeks", () => {
    expect(trendQueryParams(readTrendFilters(new URLSearchParams(), FRIDAY))).toEqual({
      from: "2026-09-25T20:30:00.000Z",
      to: "2026-10-23T20:30:00.000Z",
      grain: "week",
      timezone: "Asia/Tehran",
    });
  });

  it("sends the day after the last day as the exclusive end, and the team when one is chosen", () => {
    const params = trendQueryParams({ from: "2026-10-01", to: "2026-10-10", teamId: "team-7", grain: "month" });
    expect(params).toEqual({
      from: "2026-09-30T20:30:00.000Z",
      to: "2026-10-10T20:30:00.000Z",
      grain: "month",
      timezone: "Asia/Tehran",
      team_id: "team-7",
    });
    expect(params).not.toHaveProperty("agent_user_id");
  });
});
