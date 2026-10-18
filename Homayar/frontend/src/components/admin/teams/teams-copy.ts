import type { ErrorCodeMessages } from "@/lib/error-messages";

/** Every team page text, in the console's two languages (HOM-182). */
export const TEAMS_COPY = {
  en: {
    title: "Teams",
    intro: "Group people into teams and give them a supervisor or agent role.",
    createTeam: "Create team",

    scopeWorkspace: "Record access: whole workspace. Every member sees every record; teams only group people.",
    scopeTeam:
      "Record access: by team. Supervisors see their teams' records and agents see only their own. Owners and admins always see everything.",
    scopeLink: "Change in settings",
    scopeLoadFailed: "The record access setting could not be loaded.",

    tableLabel: "Team list",
    tableScrollLabel: "Team list, scrolls horizontally",
    columnTeam: "Team",
    columnStatus: "Status",
    columnMembers: "Members",
    columnActions: "Actions",
    active: "Active",
    inactive: "Inactive",
    membersLoadFailed: "—",
    open: "Open",
    openTeam: (name: string) => `Open team ${name}`,
    rename: "Rename",
    renameTeam: (name: string) => `Rename team ${name}`,
    deactivate: "Deactivate",
    deactivateTeam: (name: string) => `Deactivate team ${name}`,
    reactivate: "Reactivate",
    reactivateTeam: (name: string) => `Reactivate team ${name}`,
    loading: "Loading teams",
    loadFailed: "The teams could not be loaded.",
    noTeams: "No teams yet.",
    createFirst: "Create the first team to organize your people.",

    createHeading: "Create team",
    renameHeading: "Rename team",
    nameLabel: "Team name",
    create: "Create",
    save: "Save",
    cancel: "Cancel",
    teamCreated: "Team created.",
    teamRenamed: "Team renamed.",
    saveFailed: "Saving the team failed.",

    deactivateHeading: (name: string) => `Deactivate ${name}?`,
    deactivateBody:
      "Its assignments stop giving access to records. The team and its history stay, and you can reactivate it later.",
    reactivateHeading: (name: string) => `Reactivate ${name}?`,
    reactivateBody: "Its current assignments give access to records again.",
    teamDeactivated: "Team deactivated.",
    teamReactivated: "Team reactivated.",
    statusChangeFailed: "Changing the team failed.",

    close: "Close",
    assignmentsLabel: "Team assignments",
    assignmentsScrollLabel: "Team assignments, scrolls horizontally",
    columnMember: "Member",
    columnRole: "Role",
    columnFrom: "From",
    columnUntil: "Until",
    noEndDate: "No end date",
    revoked: "Revoked",
    effective: "Effective",
    startsLater: "Starts later",
    ended: "Ended",
    revoke: "Revoke",
    revokeAssignment: (name: string, role: string) => `Revoke ${name} as ${role}`,
    noAssignments: "No one is assigned to this team yet.",
    detailLoadFailed: "The team could not be loaded.",

    revokeHeading: "Revoke this assignment?",
    revokeBody: (name: string, role: string, team: string) =>
      `${name} stops being ${role} of ${team} now. The row stays in the history.`,
    revokeConfirm: "Revoke",
    assignmentRevoked: "Assignment revoked.",
    revokeFailed: "Revoking the assignment failed.",

    assignHeading: "Assign a member",
    memberLabel: "Member",
    memberPlaceholder: "Type to search members",
    roleLabel: "Team role",
    agent: "Agent",
    supervisor: "Supervisor",
    validPeriod: "Valid period (optional)",
    chooseRange: "Choose a valid period",
    assign: "Assign",
    memberAssigned: "Member assigned.",
    assignFailed: "Assigning the member failed.",
    membersFailed: "The member list could not be loaded.",
  },
  fa: {
    title: "تیم‌ها",
    intro: "افراد را در تیم‌ها گروه‌بندی کنید و به آن‌ها نقش سرپرست یا کارشناس بدهید.",
    createTeam: "ساخت تیم",

    scopeWorkspace:
      "دسترسی به رکوردها: کل فضای کاری. همهٔ اعضا همهٔ رکوردها را می‌بینند و تیم‌ها فقط افراد را گروه‌بندی می‌کنند.",
    scopeTeam:
      "دسترسی به رکوردها: بر اساس تیم. سرپرست‌ها رکوردهای تیم‌های خود را می‌بینند و کارشناس‌ها فقط رکوردهای خودشان را. مالک و مدیرها همیشه همه چیز را می‌بینند.",
    scopeLink: "تغییر در تنظیمات",
    scopeLoadFailed: "تنظیم دسترسی به رکوردها بارگیری نشد.",

    tableLabel: "فهرست تیم‌ها",
    tableScrollLabel: "فهرست تیم‌ها، با پیمایش افقی",
    columnTeam: "تیم",
    columnStatus: "وضعیت",
    columnMembers: "اعضا",
    columnActions: "عملیات",
    active: "فعال",
    inactive: "غیرفعال",
    membersLoadFailed: "—",
    open: "باز کردن",
    openTeam: (name: string) => `باز کردن تیم ${name}`,
    rename: "تغییر نام",
    renameTeam: (name: string) => `تغییر نام تیم ${name}`,
    deactivate: "غیرفعال کردن",
    deactivateTeam: (name: string) => `غیرفعال کردن تیم ${name}`,
    reactivate: "فعال کردن دوباره",
    reactivateTeam: (name: string) => `فعال کردن دوباره تیم ${name}`,
    loading: "در حال دریافت تیم‌ها",
    loadFailed: "فهرست تیم‌ها بارگیری نشد.",
    noTeams: "هنوز تیمی ساخته نشده است.",
    createFirst: "اولین تیم را بسازید تا افراد را گروه‌بندی کنید.",

    createHeading: "ساخت تیم",
    renameHeading: "تغییر نام تیم",
    nameLabel: "نام تیم",
    create: "ساختن",
    save: "ذخیره",
    cancel: "انصراف",
    teamCreated: "تیم ساخته شد.",
    teamRenamed: "نام تیم تغییر کرد.",
    saveFailed: "ذخیرهٔ تیم ناموفق بود.",

    deactivateHeading: (name: string) => `تیم ${name} غیرفعال شود؟`,
    deactivateBody:
      "تخصیص‌های آن دیگر دسترسی به رکوردها نمی‌دهند. تیم و سابقهٔ آن می‌ماند و بعداً می‌توانید آن را دوباره فعال کنید.",
    reactivateHeading: (name: string) => `تیم ${name} دوباره فعال شود؟`,
    reactivateBody: "تخصیص‌های جاری آن دوباره دسترسی به رکوردها می‌دهند.",
    teamDeactivated: "تیم غیرفعال شد.",
    teamReactivated: "تیم دوباره فعال شد.",
    statusChangeFailed: "تغییر وضعیت تیم ناموفق بود.",

    close: "بستن",
    assignmentsLabel: "تخصیص‌های تیم",
    assignmentsScrollLabel: "تخصیص‌های تیم، با پیمایش افقی",
    columnMember: "عضو",
    columnRole: "نقش",
    columnFrom: "از",
    columnUntil: "تا",
    noEndDate: "بدون تاریخ پایان",
    revoked: "لغوشده",
    effective: "مؤثر",
    startsLater: "شروع در آینده",
    ended: "پایان‌یافته",
    revoke: "لغو",
    revokeAssignment: (name: string, role: string) => `لغو تخصیص ${name} (${role})`,
    noAssignments: "هنوز کسی به این تیم اختصاص داده نشده است.",
    detailLoadFailed: "دریافت تیم ناموفق بود.",

    revokeHeading: "این تخصیص لغو شود؟",
    revokeBody: (name: string, role: string, team: string) =>
      `${name} از همین حالا دیگر ${role} تیم ${team} نیست. این ردیف در سابقه می‌ماند.`,
    revokeConfirm: "لغو تخصیص",
    assignmentRevoked: "تخصیص لغو شد.",
    revokeFailed: "لغو تخصیص ناموفق بود.",

    assignHeading: "افزودن عضو به تیم",
    memberLabel: "عضو",
    memberPlaceholder: "برای جست‌وجوی اعضا تایپ کنید",
    roleLabel: "نقش در تیم",
    agent: "کارشناس",
    supervisor: "سرپرست",
    validPeriod: "بازهٔ اعتبار (اختیاری)",
    chooseRange: "انتخاب بازهٔ اعتبار",
    assign: "افزودن",
    memberAssigned: "عضو به تیم اضافه شد.",
    assignFailed: "افزودن عضو به تیم ناموفق بود.",
    membersFailed: "فهرست اعضا بارگیری نشد.",
  },
} as const;

export type TeamsCopy = (typeof TEAMS_COPY)["en" | "fa"];

/** Error codes of backend/routers/v1/teams.py and services/team_service.py. */
export const TEAM_ERROR_CODES: ErrorCodeMessages = {
  team_conflict: {
    fa: "تیمی با این نام از قبل وجود دارد.",
    en: "A team with this name already exists.",
  },
  team_not_found: {
    fa: "این تیم دیگر وجود ندارد.",
    en: "This team no longer exists.",
  },
  assignment_conflict: {
    fa: "این عضو در همین بازه همین نقش را در این تیم دارد.",
    en: "This member already has this role in the team for an overlapping period.",
  },
  assignee_not_member: {
    fa: "این فرد دیگر عضو فعال این فضای کاری نیست.",
    en: "This person is no longer an active member of the workspace.",
  },
  invalid_validity_window: {
    fa: "تاریخ پایان باید بعد از تاریخ شروع باشد.",
    en: "The end date must be after the start date.",
  },
};

/** `supervisor` -> "Supervisor" / "سرپرست"; the label shown in tables and dialogs. */
export function teamRoleLabel(role: "supervisor" | "agent", lang: "fa" | "en") {
  return TEAMS_COPY[lang][role === "supervisor" ? "supervisor" : "agent"];
}
