import {
  CalendarDate,
  GregorianCalendar,
  fromDate,
  getLocalTimeZone,
  startOfWeek,
  toCalendar,
  toCalendarDate,
} from "@internationalized/date";

import type {
  CoverageWindow,
  OrgMember,
  OrgMemberUpdateInput,
  OrgWorkspace,
  ScopeGrant,
  WorkspaceAggregate,
} from "@/types/organizations";

/** What one membership row shows, decided in this order (SPEC 4.4). */
export type OrgMemberStatus = "revoked" | "inactive" | "active" | "scheduled" | "expired";

export function orgMemberStatus(member: OrgMember, now: Date = new Date()): OrgMemberStatus {
  if (member.revoked_at !== null) return "revoked";
  if (!member.is_active) return "inactive";
  if (member.is_effective) return "active";
  if (new Date(member.valid_from).getTime() > now.getTime()) return "scheduled";
  return "expired";
}

/**
 * A start day as the API receives it: the local midnight that starts it, as
 * a full ISO string. Works for a day of any calendar (Persian or Gregorian).
 */
export function dayStartIso(day: CalendarDate): string {
  return day.toDate(getLocalTimeZone()).toISOString();
}

/**
 * An end day as the API receives it. The picked day is included, and the
 * server window ends before `valid_until`, so this is the local midnight
 * that starts the next day (like `assignmentDatesFromRange` in team-model.ts).
 */
export function dayEndIso(day: CalendarDate): string {
  return day.add({ days: 1 }).toDate(getLocalTimeZone()).toISOString();
}

/** The instant just before `valid_until`: its calendar day is the last included day. */
export function lastIncludedIso(iso: string): string {
  return new Date(Date.parse(iso) - 1).toISOString();
}

/** The last included day of a stored end, as a Gregorian day; null for no end. */
export function lastIncludedDay(iso: string | null): CalendarDate | null {
  if (!iso) return null;
  const date = new Date(Date.parse(iso) - 1);
  return new CalendarDate(date.getFullYear(), date.getMonth() + 1, date.getDate());
}

/** "2026-11-01" for the day, whatever calendar it was picked in. */
function dayKey(day: CalendarDate) {
  return toCalendar(day, new GregorianCalendar()).toString();
}

/** True when both days are set and the end day is before the start day. One day is allowed. */
export function isWindowInvalid(fromDay: CalendarDate | null, untilDay: CalendarDate | null): boolean {
  return fromDay !== null && untilDay !== null && Date.parse(dayStartIso(untilDay)) < Date.parse(dayStartIso(fromDay));
}

/**
 * The PATCH body of the edit dialog: only what changed. Only a ticked "No end
 * date" sends `clear_valid_until` (`valid_until: null` would mean "no
 * change"). An empty date with the box unticked changes nothing: removing an
 * end date widens access, so it must be the visible choice.
 */
export function membershipPatch(
  member: Pick<OrgMember, "role" | "valid_until">,
  draft: { role: string; noEndDate: boolean; untilDay: CalendarDate | null },
): OrgMemberUpdateInput {
  const patch: OrgMemberUpdateInput = {};
  if (draft.role !== member.role) patch.role = draft.role as OrgMemberUpdateInput["role"];
  const before = lastIncludedDay(member.valid_until);
  if (draft.noEndDate) {
    if (before !== null) patch.clear_valid_until = true;
  } else if (draft.untilDay !== null && (before === null || dayKey(draft.untilDay) !== dayKey(before))) {
    patch.valid_until = dayEndIso(draft.untilDay);
  }
  return patch;
}

/** A workspace whose counts may be shown: released, with all four counts present. */
export type ReleasedAggregate = WorkspaceAggregate & {
  meetings_total: number;
  meetings_by_status: Record<string, number>;
  audio_seconds_total: number;
  action_items_total: number;
};

/**
 * Only `released` carries counts (contract 1.7.0). `masked` or a null count
 * is treated as withheld, so a count the server withheld is never shown.
 */
export function isReleased(item: WorkspaceAggregate): item is ReleasedAggregate {
  return (
    item.disclosure.status === "released" &&
    item.meetings_total !== null &&
    item.meetings_by_status !== null &&
    item.audio_seconds_total !== null &&
    item.action_items_total !== null
  );
}

export interface OrgTotals {
  /** Every workspace, released and hidden. */
  workspaces: number;
  hidden: number;
  /** The sums below cover released workspaces only. */
  meetings: number;
  audioSeconds: number;
  actionItems: number;
}

export function orgTotals(items: WorkspaceAggregate[]): OrgTotals {
  return items.reduce<OrgTotals>(
    (sum, item) =>
      isReleased(item)
        ? {
            ...sum,
            workspaces: sum.workspaces + 1,
            meetings: sum.meetings + item.meetings_total,
            audioSeconds: sum.audioSeconds + item.audio_seconds_total,
            actionItems: sum.actionItems + item.action_items_total,
          }
        : { ...sum, workspaces: sum.workspaces + 1, hidden: sum.hidden + 1 },
    { workspaces: 0, hidden: 0, meetings: 0, audioSeconds: 0, actionItems: 0 },
  );
}

/** A count from `meetings_by_status`; 0 when the status is absent. */
export function statusCount(item: ReleasedAggregate, status: string): number {
  return Object.hasOwn(item.meetings_by_status, status) ? item.meetings_by_status[status] : 0;
}

/** How many closed weeks the overview asks for; 4 is the server default. */
export type OrgWeeks = "4" | "13" | "26" | "52";

export const ORG_WEEKS: readonly OrgWeeks[] = ["4", "13", "26", "52"];

/** The aggregates grid: weeks start Saturday 00:00 in Tehran, whatever the browser's zone. */
const GRID_ZONE = "Asia/Tehran";

/**
 * `since` for N closed weeks: Saturday 00:00 Tehran of the current week,
 * minus N weeks, as ISO. The default 4 weeks sends none, so the server
 * picks its own default window.
 */
export function sinceForWeeks(weeks: OrgWeeks, now: number = Date.now()): string | null {
  if (weeks === "4") return null;
  const today = toCalendarDate(fromDate(new Date(now), GRID_ZONE));
  return startOfWeek(today, "en", "sat").subtract({ weeks: Number(weeks) }).toDate(GRID_ZONE).toISOString();
}

/**
 * The window the server used, as two days in Tehran time: its first day and
 * its last included day (`to` is exclusive, so 1 ms before it).
 */
export function windowDays(window: CoverageWindow, lang: "en" | "fa"): { from: string; last: string } {
  const format = new Intl.DateTimeFormat(lang === "fa" ? "fa-IR" : "en", { dateStyle: "medium", timeZone: GRID_ZONE });
  return { from: format.format(new Date(window.from)), last: format.format(new Date(Date.parse(window.to) - 1)) };
}

/** A scope grant with its member and workspace, when the lists still have them. */
export interface ScopeGrantRow extends ScopeGrant {
  member: OrgMember | null;
  workspace: OrgWorkspace | null;
}

export function scopeGrantRows(grants: ScopeGrant[], members: OrgMember[], workspaces: OrgWorkspace[]): ScopeGrantRow[] {
  return grants.map((grant) => ({
    ...grant,
    member: members.find((member) => member.id === grant.membership_id) ?? null,
    workspace: workspaces.find((workspace) => workspace.id === grant.workspace_id) ?? null,
  }));
}

/** The member's name, or the raw membership id when the list does not have it. */
export function grantMemberName(row: ScopeGrantRow) {
  return row.member?.display_name ?? row.membership_id;
}

export function grantWorkspaceName(row: ScopeGrantRow) {
  return row.workspace?.name ?? row.workspace_id;
}
