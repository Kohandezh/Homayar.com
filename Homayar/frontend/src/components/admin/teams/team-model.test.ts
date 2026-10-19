import { describe, expect, it } from "vitest";
import { CalendarDate } from "@internationalized/date";

import {
  assignmentDatesFromRange,
  assignmentStatus,
  effectiveMemberCount,
  isRevocable,
} from "@/components/admin/teams/team-model";
import type { TeamAssignment } from "@/types/platform";

const NOW = new Date("2026-10-18T12:00:00Z");

function assignment(overrides: Partial<TeamAssignment> = {}): TeamAssignment {
  return {
    id: "assignment-1",
    team_id: "team-1",
    user_id: "user-1",
    display_name: "Person",
    email: null,
    role: "agent",
    valid_from: "2026-01-01T00:00:00Z",
    valid_until: null,
    revoked_at: null,
    is_effective: true,
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("assignmentStatus", () => {
  it("a revoked row is revoked, whatever its window says", () => {
    expect(assignmentStatus(assignment({ revoked_at: "2026-02-01T00:00:00Z", is_effective: false }))).toBe("revoked");
  });

  it("an effective row is effective", () => {
    expect(assignmentStatus(assignment({ is_effective: true }))).toBe("effective");
  });

  it("a window that has not started starts later", () => {
    expect(
      assignmentStatus(assignment({ is_effective: false, valid_from: "2027-01-01T00:00:00Z" }), NOW),
    ).toBe("starts_later");
  });

  it("a window that is over has ended", () => {
    expect(
      assignmentStatus(
        assignment({ is_effective: false, valid_from: "2026-01-01T00:00:00Z", valid_until: "2026-06-01T00:00:00Z" }),
        NOW,
      ),
    ).toBe("ended");
  });
});

describe("isRevocable", () => {
  it("offers revoke for effective and future rows only", () => {
    expect(isRevocable("effective")).toBe(true);
    expect(isRevocable("starts_later")).toBe(true);
    expect(isRevocable("revoked")).toBe(false);
    expect(isRevocable("ended")).toBe(false);
  });
});

describe("effectiveMemberCount", () => {
  it("counts distinct users among effective assignments only", () => {
    const assignments = [
      assignment({ id: "a1", user_id: "user-1", role: "agent", is_effective: true }),
      // The same user again, as supervisor: still one person.
      assignment({ id: "a2", user_id: "user-1", role: "supervisor", is_effective: true }),
      assignment({ id: "a3", user_id: "user-2", is_effective: true }),
      // Revoked and ended rows never count.
      assignment({ id: "a4", user_id: "user-3", is_effective: false, revoked_at: "2026-02-01T00:00:00Z" }),
      assignment({
        id: "a5",
        user_id: "user-4",
        is_effective: false,
        valid_until: "2026-06-01T00:00:00Z",
      }),
    ];
    expect(effectiveMemberCount(assignments)).toBe(2);
  });

  it("is zero with no effective assignment", () => {
    expect(effectiveMemberCount([])).toBe(0);
    expect(effectiveMemberCount([assignment({ is_effective: false })])).toBe(0);
  });
});

describe("assignmentDatesFromRange", () => {
  it("spans the picked days inclusively, so the window is one day longer", () => {
    const { valid_from, valid_until } = assignmentDatesFromRange(
      new CalendarDate(2026, 10, 18),
      new CalendarDate(2026, 10, 20),
    );
    // Both values are local midnights; their distance is three whole days
    // because the picked end day (the 20th) is part of the window.
    expect(new Date(valid_until).getTime() - new Date(valid_from).getTime()).toBe(3 * 86_400_000);
    expect(new Date(valid_from).getUTCFullYear()).toBe(2026);
  });
});
