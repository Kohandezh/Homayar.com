import { describe, expect, it } from "vitest";
import { isDueAtInRange, parseEvidenceTurns, toLocalDateTimeInput } from "@/components/reviews/review-model";

describe("parseEvidenceTurns", () => {
  it("accepts the Persian digits and the Persian comma the fa hint itself shows", () => {
    expect(parseEvidenceTurns("۳، ۷")).toEqual({ values: [3, 7], error: null });
    expect(parseEvidenceTurns("۳,۷")).toEqual({ values: [3, 7], error: null });
    expect(parseEvidenceTurns("3، 7")).toEqual({ values: [3, 7], error: null });
  });

  it("sorts ascending and drops blank parts", () => {
    expect(parseEvidenceTurns("7, 3")).toEqual({ values: [3, 7], error: null });
    expect(parseEvidenceTurns("12 , , 0")).toEqual({ values: [0, 12], error: null });
  });

  it("reports each broken rule with its own error", () => {
    expect(parseEvidenceTurns("  ")).toEqual({ values: null, error: "required" });
    expect(parseEvidenceTurns("3,x")).toEqual({ values: null, error: "invalid" });
    expect(parseEvidenceTurns("-1")).toEqual({ values: null, error: "invalid" });
    expect(parseEvidenceTurns("۳، ۳")).toEqual({ values: null, error: "duplicate" });
    expect(parseEvidenceTurns(Array.from({ length: 21 }, (_, i) => String(i)).join(","))).toEqual({
      values: null,
      error: "too_many",
    });
  });
});

describe("due time helpers", () => {
  it("writes a datetime-local value in local time and validates the server's window", () => {
    const iso = "2026-11-12T09:30:00Z";
    // The local-time text reads back as the same instant.
    expect(new Date(toLocalDateTimeInput(iso)).getTime()).toBe(new Date(iso).getTime());
    expect(toLocalDateTimeInput(iso)).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);

    const past = new Date(Date.now() - 60_000).toISOString();
    const soon = new Date(Date.now() + 60_000).toISOString();
    const far = new Date(Date.now() + 91 * 24 * 60 * 60 * 1000).toISOString();
    expect(isDueAtInRange(past)).toBe(false);
    expect(isDueAtInRange(soon)).toBe(true);
    expect(isDueAtInRange(far)).toBe(false);
    expect(isDueAtInRange("not a date")).toBe(false);
  });
});
