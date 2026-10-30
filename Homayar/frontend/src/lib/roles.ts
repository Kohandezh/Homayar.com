import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";

const ROLE_LABELS: Record<"fa" | "en", Record<WorkspaceRole, string>> = {
  en: { owner: "Owner", admin: "Administrator", member: "Member", viewer: "Viewer" },
  fa: { owner: "مالک", admin: "مدیر", member: "عضو", viewer: "بیننده" },
};

export function roleLabel(role: WorkspaceRole, lang: "fa" | "en") {
  return Object.hasOwn(ROLE_LABELS[lang], role) ? ROLE_LABELS[lang][role] : role;
}

const PERMISSIONS = [
  "meeting:read",
  "meeting:create",
  "meeting:update",
  "meeting:delete",
  "share:manage",
  "member:read",
  "member:manage",
  "team:read",
  "team:manage",
  "workspace:manage",
  "billing:read",
  "billing:manage",
  "audit:read",
  "api_key:manage",
  "legal_hold:manage",
  "pii:original_access",
  "ai_policy:manage",
  "scorecard:read",
  "scorecard:manage",
] as const;

export type Permission = (typeof PERMISSIONS)[number];

const KNOWN_PERMISSIONS: ReadonlySet<string> = new Set(PERMISSIONS);

/**
 * The product's authorization matrix (backend/security/authorization.py
 * ROLE_PERMISSIONS). It only decides what the console offers; the API still
 * enforces every permission and its 403 is always handled (gap G4).
 */
export const ROLE_PERMISSIONS: Record<WorkspaceRole, readonly Permission[]> = {
  owner: PERMISSIONS,
  admin: [
    "meeting:read",
    "meeting:create",
    "meeting:update",
    "meeting:delete",
    "share:manage",
    "member:read",
    "member:manage",
    "team:read",
    "team:manage",
    "workspace:manage",
    "billing:read",
    "audit:read",
    "api_key:manage",
    "scorecard:read",
    "scorecard:manage",
  ],
  member: [
    "meeting:read",
    "meeting:create",
    "meeting:update",
    "share:manage",
    "member:read",
    "team:read",
    "scorecard:read",
  ],
  viewer: ["meeting:read", "member:read", "team:read", "scorecard:read"],
};

const NO_PERMISSIONS: ReadonlySet<Permission> = new Set();

/** Own keys only: a role like "constructor" must not reach Object.prototype. */
function permissionsOfRole(role: WorkspaceRole): readonly Permission[] {
  return Object.hasOwn(ROLE_PERMISSIONS, role) ? ROLE_PERMISSIONS[role] : [];
}

function isPermission(value: unknown): value is Permission {
  return typeof value === "string" && KNOWN_PERMISSIONS.has(value);
}

/**
 * What the selected workspace allows. A `permissions` array from the session
 * is the only source when present (an empty one grants nothing); without it
 * the role map decides. Unknown strings, unknown roles and any other value
 * grant nothing.
 */
export function permissionsFor(workspace: AuthWorkspace | null | undefined): ReadonlySet<Permission> {
  if (!workspace) return NO_PERMISSIONS;
  const { permissions } = workspace;
  if (Array.isArray(permissions)) return new Set(permissions.filter(isPermission));
  if (permissions !== undefined) return NO_PERMISSIONS;
  return new Set(permissionsOfRole(workspace.role));
}

export type Capabilities = ReturnType<typeof capabilitiesFrom>;

/** The capability flags the console checks, from a permission set. */
export function capabilitiesFrom(permissions: ReadonlySet<Permission>) {
  return {
    createMeeting: permissions.has("meeting:create"),
    updateMeeting: permissions.has("meeting:update"),
    deleteMeeting: permissions.has("meeting:delete"),
    manageShares: permissions.has("share:manage"),
    readBilling: permissions.has("billing:read"),
    manageWorkspace: permissions.has("workspace:manage"),
    readOriginalTranscript: permissions.has("pii:original_access"),
  };
}

/** Affordances of a role, from the matrix alone. */
export function roleCan(role: WorkspaceRole | null | undefined): Capabilities {
  return capabilitiesFrom(new Set(role ? permissionsOfRole(role) : []));
}

/** Affordances of the selected workspace (its permissions, else its role). */
export function workspaceCan(workspace: AuthWorkspace | null | undefined): Capabilities {
  return capabilitiesFrom(permissionsFor(workspace));
}
