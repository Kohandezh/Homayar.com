import { describe, expect, it } from "vitest";
import {
  countMeetings,
  degradedChecks,
  groupMeetingCounts,
  quotaPercent,
  quotaRows,
} from "@/components/admin/overview/overview-model";
import type { MeetingStatus, UsageSnapshot } from "@/types/platform";

function statuses(...values: MeetingStatus[]) {
  return values.map((status) => ({ status }));
}

/** A full server counts object; missing keys are overridden away in the tests that need them. */
function meetingCounts(overrides: Partial<Record<MeetingStatus, number>> = {}): Record<MeetingStatus, number> {
  return {
    uploading: 0,
    uploaded: 0,
    queued: 0,
    processing: 0,
    transcribing: 0,
    diarizing: 0,
    analyzing: 0,
    completed: 0,
    partial: 0,
    failed: 0,
    cancelled: 0,
    ...overrides,
  };
}

function usage(metric: string, used: number, limit: number | null, reserved = 0): UsageSnapshot {
  return { metric, used, reserved, limit, remaining: limit === null ? null : limit - used - reserved };
}

describe("countMeetings", () => {
  it("counts each group and leaves cancelled meetings out", () => {
    const counts = countMeetings(
      statuses(
        "completed",
        "completed",
        "uploading",
        "uploaded",
        "queued",
        "processing",
        "transcribing",
        "diarizing",
        "analyzing",
        "failed",
        "partial",
        "cancelled",
        "cancelled",
      ),
    );
    expect(counts).toEqual({ completed: 2, inProgress: 7, failed: 1, partial: 1 });
  });

  it("gives zeros for no meetings", () => {
    expect(countMeetings([])).toEqual({ completed: 0, inProgress: 0, failed: 0, partial: 0 });
  });
});

describe("groupMeetingCounts", () => {
  it("groups server counts, in progress being every nonterminal status", () => {
    const counts = meetingCounts({
      completed: 120,
      failed: 3,
      partial: 2,
      queued: 4,
      transcribing: 1,
      analyzing: 2,
      cancelled: 5,
    });
    expect(groupMeetingCounts(counts)).toEqual({ completed: 120, inProgress: 7, failed: 3, partial: 2 });
  });

  it("counts every nonterminal status, not only the active ones", () => {
    const counts = meetingCounts({ uploading: 1, uploaded: 2, processing: 3, diarizing: 4 });
    expect(groupMeetingCounts(counts)).toEqual({ completed: 0, inProgress: 10, failed: 0, partial: 0 });
  });

  it("leaves cancelled out of every group", () => {
    expect(groupMeetingCounts(meetingCounts({ cancelled: 2 }))).toEqual({
      completed: 0,
      inProgress: 0,
      failed: 0,
      partial: 0,
    });
  });

  it("treats a missing key as zero", () => {
    expect(groupMeetingCounts({ completed: 2 } as Record<MeetingStatus, number>)).toEqual({
      completed: 2,
      inProgress: 0,
      failed: 0,
      partial: 0,
    });
  });

  it("gives zeros for all-zero counts", () => {
    expect(groupMeetingCounts(meetingCounts())).toEqual({ completed: 0, inProgress: 0, failed: 0, partial: 0 });
  });
});

describe("quotaPercent", () => {
  it("rounds, clamps and treats a zero limit as full", () => {
    expect(quotaPercent(79, 100)).toBe(79);
    expect(quotaPercent(80, 100)).toBe(80);
    expect(quotaPercent(795, 1000)).toBe(80);
    expect(quotaPercent(250, 100)).toBe(100);
    expect(quotaPercent(0, 100)).toBe(0);
    expect(quotaPercent(0, 0)).toBe(100);
  });
});

describe("quotaRows", () => {
  it("keeps the three known metrics in a fixed order and drops the others", () => {
    const rows = quotaRows([
      usage("seats", 2, 5),
      usage("storage_bytes", 1, 10),
      usage("audio_seconds", 600, 1800, 90),
      usage("concurrent_jobs", 1, null),
    ]);
    expect(rows.map((row) => row.metric)).toEqual(["audio_seconds", "concurrent_jobs", "seats"]);
    expect(rows[0]).toMatchObject({ consumed: 690, limit: 1800, percent: 38, nearLimit: false });
    expect(rows[1]).toMatchObject({ consumed: 1, limit: null, percent: null, nearLimit: false });
  });

  it("marks near limit from 80 percent, using the shown (rounded) percent", () => {
    expect(quotaRows([usage("seats", 79, 100)])[0].nearLimit).toBe(false);
    expect(quotaRows([usage("seats", 80, 100)])[0].nearLimit).toBe(true);
    expect(quotaRows([usage("seats", 70, 100, 10)])[0].nearLimit).toBe(true);
    expect(quotaRows([usage("audio_seconds", 1590, 2000)])[0]).toMatchObject({ percent: 80, nearLimit: true });
    expect(quotaRows([usage("seats", 0, 0)])[0]).toMatchObject({ percent: 100, nearLimit: true });
  });

  it("is empty when no known metric is present", () => {
    expect(quotaRows([usage("storage_bytes", 1, 10)])).toEqual([]);
    expect(quotaRows([])).toEqual([]);
  });
});

describe("degradedChecks", () => {
  it("lists the checks that are not ready, database before redis", () => {
    expect(degradedChecks({ redis: "unavailable", database: "unavailable" })).toEqual(["database", "redis"]);
    expect(degradedChecks({ database: "ready", redis: "unavailable" })).toEqual(["redis"]);
    expect(degradedChecks({ database: "ready", redis: "ready" })).toEqual([]);
  });

  it("adds other failing checks after database and redis, in the order received", () => {
    expect(
      degradedChecks({ queue: "unavailable", database: "ready", redis: "unavailable", storage: "unavailable", cache: "ready" }),
    ).toEqual(["redis", "queue", "storage"]);
    expect(degradedChecks({ storage: "unavailable", database: "unavailable" })).toEqual(["database", "storage"]);
    expect(degradedChecks({})).toEqual([]);
  });
});
