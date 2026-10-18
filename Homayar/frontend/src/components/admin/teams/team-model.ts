import type { CalendarDate } from "@internationalized/date";
import { getLocalTimeZone } from "@internationalized/date";

import type { TeamAssignment } from "@/types/platform";

/** What one assignment row shows, in the order the page checks them. */
export type AssignmentStatus = "revoked" | "effective" | "starts_later" | "ended";

/**
 * The status of one assignment row. A revoked row is `revoked` whatever its
 * window says; an effective one is current; a future `valid_from` has not
 * started; anything else has ended.
 */
export function assignmentStatus(assignment: TeamAssignment, now: Date = new Date()): AssignmentStatus {
  if (assignment.revoked_at !== null) return "revoked";
  if (assignment.is_effective) return "effective";
  if (new Date(assignment.valid_from).getTime() > now.getTime()) return "starts_later";
  return "ended";
}

/** Only a current or not-yet-started assignment can still be revoked with effect. */
export function isRevocable(status: AssignmentStatus): boolean {
  return status === "effective" || status === "starts_later";
}

/**
 * The member count of a team: distinct users with at least one effective
 * assignment. A second role of the same user counts once; revoked and ended
 * rows do not count.
 */
export function effectiveMemberCount(assignments: TeamAssignment[]): number {
  const users = new Set<string>();
  for (const assignment of assignments) {
    if (assignment.is_effective) users.add(assignment.user_id);
  }
  return users.size;
}

/**
 * The validity window the server stores for a picked day range: both ends
 * as ISO date-times, with the picked end day included (the server window is
 * exclusive at its end, so it gets the next midnight).
 */
export function assignmentDatesFromRange(start: CalendarDate, end: CalendarDate) {
  return {
    valid_from: start.toDate(getLocalTimeZone()).toISOString(),
    valid_until: end.add({ days: 1 }).toDate(getLocalTimeZone()).toISOString(),
  };
}
