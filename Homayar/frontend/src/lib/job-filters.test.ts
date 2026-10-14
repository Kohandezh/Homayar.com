import { describe, expect, it } from "vitest";
import {
  hasJobFilters,
  JOB_PAGE_SIZE,
  jobQueryParams,
  NO_JOB_FILTERS,
  readJobFilters,
  writeJobFilters,
  type JobFilters,
} from "@/lib/job-filters";

function filters(overrides: Partial<JobFilters> = {}): JobFilters {
  return { ...NO_JOB_FILTERS, ...overrides };
}

describe("processing filters in the URL", () => {
  it("reads a repeated state and the stage", () => {
    const search = new URLSearchParams("state=failed&state=dead_letter&stage=transcribing");
    expect(readJobFilters(search)).toEqual({
      states: ["failed", "dead_letter"],
      stage: "transcribing",
    });
  });

  it("drops an unknown state, so a hand-edited link still loads", () => {
    const search = new URLSearchParams("state=exploded&state=queued");
    expect(readJobFilters(search)).toEqual(filters({ states: ["queued"] }));
  });

  it("drops a state the filter bar cannot show, so no filter stays invisible", () => {
    const search = new URLSearchParams("state=completed&state=cancelled&state=failed");
    expect(readJobFilters(search)).toEqual(filters({ states: ["failed"] }));
  });

  it("keeps a repeated state once", () => {
    const search = new URLSearchParams("state=failed&state=failed");
    expect(readJobFilters(search).states).toEqual(["failed"]);
  });

  it("drops a stage longer than the API accepts", () => {
    const search = new URLSearchParams(`stage=${"s".repeat(41)}`);
    expect(readJobFilters(search)).toEqual(filters());
    expect(hasJobFilters(readJobFilters(search))).toBe(false);
  });

  it("writes only the chosen filters and keeps unrelated query parameters", () => {
    const base = new URLSearchParams("tab=jobs&state=running&stage=analyzing");
    const written = writeJobFilters(filters({ states: ["failed", "dead_letter"] }), base);
    expect(written.getAll("state")).toEqual(["failed", "dead_letter"]);
    expect(written.get("tab")).toBe("jobs");
    // Cleared filters are removed instead of written as blanks.
    expect(written.has("stage")).toBe(false);
  });

  it("round-trips the filters through the URL", () => {
    const chosen = filters({ states: ["running", "queued"], stage: "retry_scheduled" });
    expect(readJobFilters(writeJobFilters(chosen))).toEqual(chosen);
  });

  it("says when nothing is filtered", () => {
    expect(hasJobFilters(filters())).toBe(false);
    expect(hasJobFilters(filters({ states: ["failed"] }))).toBe(true);
    expect(hasJobFilters(filters({ stage: "asr" }))).toBe(true);
  });
});

describe("the request for one page of jobs", () => {
  it("asks for the page size and nothing else when nothing is filtered", () => {
    expect(jobQueryParams(filters())).toEqual({ limit: JOB_PAGE_SIZE });
  });

  it("sends every chosen filter and the cursor", () => {
    const params = jobQueryParams(filters({ states: ["failed", "dead_letter"], stage: "asr" }), {
      cursor: "cursor-2",
    });
    expect(params).toEqual({
      limit: JOB_PAGE_SIZE,
      cursor: "cursor-2",
      state: ["failed", "dead_letter"],
      stage: "asr",
    });
  });

  it("copies the states, so the request cannot change the filters", () => {
    const chosen = filters({ states: ["failed"] });
    const params = jobQueryParams(chosen);
    params.state?.push("queued");
    expect(chosen.states).toEqual(["failed"]);
  });
});
