import { afterEach, describe, expect, it, vi } from "vitest";

import { describeHoldError } from "@/components/admin/privacy/hold-errors";
import { labelOf, PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import {
  formatReviewDay,
  identifierKey,
  isReviewDue,
  isValidReason,
} from "@/components/admin/privacy/privacy-model";
import { formatDay } from "@/lib/format";
import { apiError } from "@/test/fixtures";

describe("privacy model", () => {
  it("accepts a reason of 3 to 200 characters once trimmed", () => {
    expect(isValidReason("ab")).toBe(false);
    expect(isValidReason("  ab  ")).toBe(false);
    expect(isValidReason("abc")).toBe(true);
    expect(isValidReason("x".repeat(200))).toBe(true);
    expect(isValidReason("x".repeat(201))).toBe(false);
  });

  it("marks a hold due when its review date is at or before now", () => {
    const now = new Date("2026-10-18T10:00:00Z");
    expect(isReviewDue("2026-10-18T10:00:00Z", now)).toBe(true);
    expect(isReviewDue("2026-10-17T00:00:00Z", now)).toBe(true);
    expect(isReviewDue("2026-10-18T10:00:01Z", now)).toBe(false);
  });

  it("puts only hold_review_invalid under the date field, in both languages", () => {
    const date = describeHoldError(apiError(409, "hold_review_invalid"), "fa", "x");
    expect(date.onReviewDate).toBe(true);
    expect(date.text).toBe("تاریخ بازبینی را در آینده و حداکثر ۱۰ سال بعد از امروز انتخاب کنید.");
    expect(describeHoldError(apiError(409, "not_held"), "en", "x").onReviewDate).toBe(false);
  });

  it("shows an unknown lifecycle value as sent", () => {
    expect(labelOf(PRIVACY_COPY.en.states, "legal_hold")).toBe("On legal hold");
    expect(labelOf(PRIVACY_COPY.en.states, "archived")).toBe("archived");
    expect(labelOf(PRIVACY_COPY.en.states, "constructor")).toBe("constructor");
  });
});

describe("review day and identifier key", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("formats a review date as its UTC day on both sides of UTC", () => {
    for (const zone of ["America/Los_Angeles", "Asia/Tehran", "Pacific/Kiritimati", "UTC"]) {
      vi.stubEnv("TZ", zone);
      expect(formatReviewDay("2027-01-16T00:00:00Z", "en")).toBe("Jan 16, 2027");
      expect(formatReviewDay("2027-01-16T00:00:00+00:00", "en")).toBe("Jan 16, 2027");
    }
    vi.stubEnv("TZ", "America/Los_Angeles");
    // The shared helper alone shows the local day, one day early here.
    expect(formatDay("2027-01-16T00:00:00Z", "en")).toBe("Jan 15, 2027");
    expect(formatReviewDay("2027-01-16T00:00:00Z", "fa")).toBe(formatDay("2027-01-16T12:00:00", "fa"));
  });

  it("compares e-mails without case and other identifiers as typed", () => {
    expect(identifierKey("Owner@Demo.example.com")).toBe("owner@demo.example.com");
    expect(identifierKey("IR062960000000100324200001")).toBe("IR062960000000100324200001");
    expect(identifierKey("09121234567")).toBe("09121234567");
  });
});
