import { describe, expect, it } from "vitest";
import {
  hasIngestionFilters,
  ingestionQueryParams,
  isFromAfterTo,
  nextBlockedByFrom,
  NO_INGESTION_FILTERS,
  readIngestionFilters,
  visibleIngestionRows,
  writeIngestionFilters,
  type IngestionFilters,
} from "@/lib/ingestion-filters";

function filters(overrides: Partial<IngestionFilters> = {}): IngestionFilters {
  return { ...NO_INGESTION_FILTERS, ...overrides };
}

/** A fixed zone, so the day-boundary maths does not depend on the machine. */
const TEHRAN = "Asia/Tehran";

describe("ingestion filters in the URL", () => {
  it("reads the source, the state and both days", () => {
    const search = new URLSearchParams("source=file_upload&state=rejected&from=2026-10-03&to=2026-10-05");
    expect(readIngestionFilters(search)).toEqual({
      source: "file_upload",
      state: "rejected",
      from: "2026-10-03",
      to: "2026-10-05",
    });
  });

  it("drops a source the API does not know, so a hand-edited link still loads", () => {
    const search = new URLSearchParams("source=portal_drop");
    expect(readIngestionFilters(search)).toEqual(filters());
  });

  it("drops a state the page cannot filter by", () => {
    const search = new URLSearchParams("state=exploded");
    expect(readIngestionFilters(search)).toEqual(filters());
  });

  it("drops a day that is not a real Gregorian date", () => {
    const search = new URLSearchParams("from=2026-13-40&to=10-05-2026");
    expect(readIngestionFilters(search)).toEqual(filters());
  });

  it("writes only the chosen filters and keeps unrelated query parameters", () => {
    const base = new URLSearchParams("tab=ing&source=api&state=accepted");
    const written = writeIngestionFilters(filters({ source: "file_upload", to: "2026-10-05" }), base);
    expect(written.get("source")).toBe("file_upload");
    expect(written.get("to")).toBe("2026-10-05");
    expect(written.get("tab")).toBe("ing");
    // Cleared filters are removed instead of written as blanks.
    expect(written.has("state")).toBe(false);
    expect(written.has("from")).toBe(false);
  });

  it("round-trips the filters through the URL", () => {
    const chosen = filters({ source: "voicemail", state: "rejected", from: "2026-10-03", to: "2026-10-05" });
    expect(readIngestionFilters(writeIngestionFilters(chosen))).toEqual(chosen);
  });

  it("says when nothing is filtered", () => {
    expect(hasIngestionFilters(filters())).toBe(false);
    expect(hasIngestionFilters(filters({ source: "api" }))).toBe(true);
    expect(hasIngestionFilters(filters({ state: "accepted" }))).toBe(true);
    expect(hasIngestionFilters(filters({ from: "2026-10-03" }))).toBe(true);
    expect(hasIngestionFilters(filters({ to: "2026-10-05" }))).toBe(true);
  });

  it("notices a start day after the end day, and only then", () => {
    expect(isFromAfterTo(filters({ from: "2026-10-06", to: "2026-10-05" }))).toBe(true);
    expect(isFromAfterTo(filters({ from: "2026-10-05", to: "2026-10-05" }))).toBe(false);
    expect(isFromAfterTo(filters({ from: "2026-10-03", to: "2026-10-05" }))).toBe(false);
    expect(isFromAfterTo(filters({ from: "2026-10-06" }))).toBe(false);
    expect(isFromAfterTo(filters({ to: "2026-10-05" }))).toBe(false);
  });
});

describe("the request for one page of ingestions", () => {
  it("asks for the page size and nothing else when nothing is filtered", () => {
    expect(ingestionQueryParams(filters(), { timeZone: TEHRAN })).toEqual({ limit: 25 });
  });

  it("sends the chosen source as the server's source_type", () => {
    expect(ingestionQueryParams(filters({ source: "meeting_bot" }), { timeZone: TEHRAN })).toEqual({
      limit: 25,
      source_type: "meeting_bot",
    });
  });

  it("turns the end day into the exclusive cursor of the first page, in the reader's zone", () => {
    expect(ingestionQueryParams(filters({ to: "2026-10-05" }), { timeZone: TEHRAN })).toEqual({
      limit: 25,
      // The midnight that starts 2026-10-06 in Tehran.
      before: "2026-10-05T20:30:00.000Z",
    });
  });

  it("sends a cursor the server handed back verbatim, even beside an end day", () => {
    expect(
      ingestionQueryParams(filters({ to: "2026-10-05" }), {
        before: "2026-09-01T00:00:00.000Z",
        timeZone: TEHRAN,
      }),
    ).toEqual({ limit: 25, before: "2026-09-01T00:00:00.000Z" });
  });
});

describe("the filters the console applies itself", () => {
  const rows = [
    { status: "accepted", received_at: "2026-10-04T12:00:00Z" },
    { status: "rejected", received_at: "2026-10-03T00:00:00Z" },
    { status: "accepted", received_at: "2026-10-02T20:29:59Z" },
  ];

  it("keeps only the rejected rows of the loaded page", () => {
    const kept = visibleIngestionRows(rows, filters({ state: "rejected" }), TEHRAN);
    expect(kept).toEqual([rows[1]]);
  });

  it("hides rows from before the start of the start day", () => {
    // 2026-10-03 starts at 2026-10-02T20:30:00Z in Tehran, so the third row
    // is one second too old and the second row only just counts.
    const kept = visibleIngestionRows(rows, filters({ from: "2026-10-03" }), TEHRAN);
    expect(kept).toEqual([rows[0], rows[1]]);
  });

  it("keeps every row without the client-side filters", () => {
    expect(visibleIngestionRows(rows, filters(), TEHRAN)).toEqual(rows);
  });

  it("stops offering the next page once the page's oldest row is older than the start day", () => {
    expect(nextBlockedByFrom(rows, filters({ from: "2026-10-03" }), TEHRAN)).toBe(true);
    expect(nextBlockedByFrom(rows, filters({ from: "2026-10-02" }), TEHRAN)).toBe(false);
    expect(nextBlockedByFrom(rows, filters(), TEHRAN)).toBe(false);
    expect(nextBlockedByFrom([], filters({ from: "2026-10-03" }), TEHRAN)).toBe(false);
  });
});
