import { describe, expect, it } from "vitest";
import {
  hasVoicemailFilters,
  NO_VOICEMAIL_FILTERS,
  readVoicemailFilters,
  voicemailQueryParams,
  VOICEMAIL_PAGE_SIZE,
  writeVoicemailFilters,
  type VoicemailFilters,
} from "@/lib/voicemail-filters";

function filters(overrides: Partial<VoicemailFilters> = {}): VoicemailFilters {
  return { ...NO_VOICEMAIL_FILTERS, ...overrides };
}

describe("voicemail filters in the URL", () => {
  it("reads the priority and the category", () => {
    const search = new URLSearchParams("priority=urgent&category=billing");
    expect(readVoicemailFilters(search)).toEqual(filters({ priority: "urgent", category: "billing" }));
  });

  it("drops an unknown priority, so a hand-edited link still loads", () => {
    const search = new URLSearchParams("priority=asap&category=billing");
    expect(readVoicemailFilters(search)).toEqual(filters({ category: "billing" }));
    expect(hasVoicemailFilters(readVoicemailFilters(search))).toBe(true);
  });

  it("drops an unknown category, so no filter stays invisible", () => {
    const search = new URLSearchParams("priority=low&category=mystery");
    expect(readVoicemailFilters(search)).toEqual(filters({ priority: "low" }));
  });

  it("treats a blank value as no filter", () => {
    const search = new URLSearchParams("priority=&category=");
    expect(readVoicemailFilters(search)).toEqual(filters());
    expect(hasVoicemailFilters(readVoicemailFilters(search))).toBe(false);
  });

  it("writes only the chosen filters and keeps unrelated query parameters", () => {
    const base = new URLSearchParams("voicemail=vm-1&priority=low&category=spam");
    const written = writeVoicemailFilters(filters({ priority: "urgent" }), base);
    expect(written.get("priority")).toBe("urgent");
    expect(written.get("voicemail")).toBe("vm-1");
    // Cleared filters are removed instead of written as blanks.
    expect(written.has("category")).toBe(false);
  });

  it("round-trips the filters through the URL", () => {
    const chosen = filters({ priority: "normal", category: "appointment" });
    expect(readVoicemailFilters(writeVoicemailFilters(chosen))).toEqual(chosen);
  });

  it("says when nothing is filtered", () => {
    expect(hasVoicemailFilters(filters())).toBe(false);
    expect(hasVoicemailFilters(filters({ priority: "low" }))).toBe(true);
    expect(hasVoicemailFilters(filters({ category: "other" }))).toBe(true);
  });
});

describe("the request for one page of voicemails", () => {
  it("asks for the page size and nothing else when nothing is filtered", () => {
    expect(voicemailQueryParams(filters())).toEqual({ limit: VOICEMAIL_PAGE_SIZE });
  });

  it("sends every chosen filter and the cursor", () => {
    const params = voicemailQueryParams(filters({ priority: "urgent", category: "billing" }), {
      before: "2026-10-18T12:00:00Z",
    });
    expect(params).toEqual({
      limit: VOICEMAIL_PAGE_SIZE,
      before: "2026-10-18T12:00:00Z",
      priority: "urgent",
      category: "billing",
    });
  });
});
