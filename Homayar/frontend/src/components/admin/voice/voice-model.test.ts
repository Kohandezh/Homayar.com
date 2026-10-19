import { describe, expect, it } from "vitest";

import { capacityPercent, heartbeatAge, isNearLimit } from "@/components/admin/voice/voice-model";

const NOW = Date.parse("2026-10-18T12:00:00Z");

function ago(seconds: number) {
  return new Date(NOW - seconds * 1000).toISOString();
}

describe("capacityPercent", () => {
  it("is held / limit * 100, capped at 100", () => {
    expect(capacityPercent(0, 200)).toBe(0);
    expect(capacityPercent(50, 200)).toBe(25);
    expect(capacityPercent(1, 3)).toBeCloseTo(33.333, 3);
    expect(capacityPercent(250, 200)).toBe(100);
  });

  it("counts a zero limit as full", () => {
    expect(capacityPercent(0, 0)).toBe(100);
  });
});

describe("isNearLimit", () => {
  it("starts at 80 percent", () => {
    expect(isNearLimit(capacityPercent(159, 200))).toBe(false);
    expect(isNearLimit(capacityPercent(160, 200))).toBe(true);
    expect(isNearLimit(100)).toBe(true);
  });
});

describe("heartbeatAge", () => {
  it("uses whole seconds under a minute", () => {
    expect(heartbeatAge(ago(0), NOW)).toEqual({ unit: "s", value: 0 });
    expect(heartbeatAge(ago(59.9), NOW)).toEqual({ unit: "s", value: 59 });
  });

  it("uses whole minutes under an hour", () => {
    expect(heartbeatAge(ago(60), NOW)).toEqual({ unit: "min", value: 1 });
    expect(heartbeatAge(ago(3599), NOW)).toEqual({ unit: "min", value: 59 });
  });

  it("uses whole hours after that", () => {
    expect(heartbeatAge(ago(3600), NOW)).toEqual({ unit: "h", value: 1 });
    expect(heartbeatAge(ago(30 * 3600 + 59 * 60), NOW)).toEqual({ unit: "h", value: 30 });
  });

  it("shows a time in the future as 0 s", () => {
    expect(heartbeatAge(new Date(NOW + 45_000).toISOString(), NOW)).toEqual({ unit: "s", value: 0 });
  });

  it("reads an offset time like the backend sends", () => {
    expect(heartbeatAge("2026-10-18T15:29:30+03:30", NOW)).toEqual({ unit: "s", value: 30 });
  });

  it("gives null for a time it cannot read", () => {
    expect(heartbeatAge("not a time", NOW)).toBeNull();
  });
});
