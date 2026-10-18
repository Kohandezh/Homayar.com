import type { DelegatedWorkspaceRole, GrantableOrganizationRole, OrganizationRole } from "@/types/organizations";

const ORG_PERMISSIONS = [
  "organization:read",
  "organization:member:read",
  "organization:member:manage",
  "organization:workspace:read",
  "organization:workspace:create",
  "organization:workspace:administer",
  "organization:analytics:read",
  "organization:records:read",
] as const;

export type OrgPermission = (typeof ORG_PERMISSIONS)[number];

/**
 * The organization authorization matrix (backend/security/authorization.py
 * ORGANIZATION_ROLE_PERMISSIONS). It only decides what the console offers;
 * the API still enforces every permission.
 */
const ORG_ROLE_PERMISSIONS: Record<OrganizationRole, readonly OrgPermission[]> = {
  owner: ORG_PERMISSIONS,
  admin: ORG_PERMISSIONS,
  analyst: ["organization:read", "organization:workspace:read", "organization:analytics:read", "organization:records:read"],
  executive: ["organization:read", "organization:workspace:read", "organization:analytics:read"],
};

/** ORGANIZATION_GRANTABLE_ROLES: nobody grants `owner`, and an admin cannot make another admin. */
const ORG_GRANTABLE_ROLES: Record<OrganizationRole, readonly GrantableOrganizationRole[]> = {
  owner: ["admin", "analyst", "executive"],
  admin: ["analyst", "executive"],
  analyst: [],
  executive: [],
};

/** DELEGATED_WORKSPACE_ROLES: the workspace roles an org role may install. */
const DELEGATED_WORKSPACE_ROLES: Record<OrganizationRole, readonly DelegatedWorkspaceRole[]> = {
  owner: ["admin", "member", "viewer"],
  admin: ["admin", "member", "viewer"],
  analyst: [],
  executive: [],
};

const NO_PERMISSIONS: ReadonlySet<OrgPermission> = new Set();

/** Own keys only: a role like "constructor" must not reach Object.prototype. */
function isOrgRole(role: string | null | undefined): role is OrganizationRole {
  return typeof role === "string" && Object.hasOwn(ORG_ROLE_PERMISSIONS, role);
}

/** What an org role allows. An unknown role, or none, grants nothing. */
export function orgPermissionsFor(role: string | null | undefined): ReadonlySet<OrgPermission> {
  return isOrgRole(role) ? new Set(ORG_ROLE_PERMISSIONS[role]) : NO_PERMISSIONS;
}

/** The roles the actor's org role may give, in the order the Select shows them. */
export function grantableOrgRoles(role: string | null | undefined): readonly GrantableOrganizationRole[] {
  return isOrgRole(role) ? ORG_GRANTABLE_ROLES[role] : [];
}

/** The workspace roles the actor's org role may install in one workspace. */
export function delegatedWorkspaceRoles(role: string | null | undefined): readonly DelegatedWorkspaceRole[] {
  return isOrgRole(role) ? DELEGATED_WORKSPACE_ROLES[role] : [];
}

/**
 * The manage rule (`can_manage_organization_member`) plus one console rule:
 * a revoked row offers nothing. The target must not be the actor, must not be
 * an owner, and must hold a role the actor may grant.
 */
export function canManageOrgMember(
  actorRole: string | null | undefined,
  actorUserId: string | null | undefined,
  target: { user_id: string; role: string; revoked_at: string | null },
): boolean {
  if (target.revoked_at !== null) return false;
  if (!actorUserId || target.user_id === actorUserId) return false;
  if (target.role === "owner") return false;
  return (grantableOrgRoles(actorRole) as readonly string[]).includes(target.role);
}

const ORG_ROLE_LABELS: Record<"fa" | "en", Record<OrganizationRole, string>> = {
  en: { owner: "Owner", admin: "Administrator", analyst: "Analyst", executive: "Executive" },
  fa: { owner: "مالک", admin: "مدیر", analyst: "تحلیلگر", executive: "مدیر ارشد" },
};

/** An org role as a label; an unknown role is shown as sent, never guessed. */
export function orgRoleLabel(role: string, lang: "fa" | "en") {
  return isOrgRole(role) ? ORG_ROLE_LABELS[lang][role] : role;
}
