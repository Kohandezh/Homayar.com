import { describe, expect, it } from "vitest";
import { parseTimecodeToMs } from "@/lib/time-range";

describe("parseTimecodeToMs", () => {
  it.each([
    ["1:05", 65_000],
    ["0:30", 30_000],
    ["12:34", 754_000],
    ["3600", 3_600_000],
    ["45", 45_000],
    ["1:02:03", 3_723_000],
    ["0:00", 0],
  ])("parses %s into %d ms", (input, expected) => {
    expect(parseTimecodeToMs(input)).toBe(expected);
  });

  it.each([
    ["abc"],
    ["1:75"],
    ["1:02:75"],
    ["-5"],
    ["1.5"],
    ["1:"],
    [":30"],
    ["1:2:3:4"],
    ["1:5"],
    ["1:02:3"],
    ["1:2:03"],
    [""],
    ["   "],
  ])("refuses %s", (input) => {
    expect(parseTimecodeToMs(input)).toBeNull();
  });

  it("accepts Persian and Arabic-Indic digits and surrounding spaces", () => {
    expect(parseTimecodeToMs(" ۱:۰۵ ")).toBe(65_000);
    expect(parseTimecodeToMs("١:٠٥")).toBe(65_000);
  });
});
