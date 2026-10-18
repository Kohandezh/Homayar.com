/**
 * The organization API contracts (backend/schemas/organizations.py). Every
 * route is under /api/v1/organizations; the organization always comes from
 * the path, never from a body.
 */

/** A role string as the server sends it; `src/lib/org-roles.ts` decides what it allows. */
export type OrganizationRole = "owner" | "admin" | "analyst" | "executive";

/** The roles a grant may give (nobody grants `owner`). */
export type GrantableOrganizationRole = "admin" | "analyst" | "executive";

/** The workspace roles a delegated install may give. */
export type DelegatedWorkspaceRole = "admin" | "member" | "viewer";

export interface OrganizationSummary {
  id: string;
  name: string;
  slug: string;
  status: string;
  role: string;
  valid_until: string | null;
}

export interface OrganizationListResponse {
  items: OrganizationSummary[];
  total: number;
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  status: string;
  role: string;
  unrestricted: boolean;
  workspace_count: number;
  created_at: string;
}

export interface OrgMember {
  id: string;
  organization_id: string;
  user_id: string;
  email: string | null;
  display_name: string;
  role: string;
  is_active: boolean;
  valid_from: string;
  valid_until: string | null;
  revoked_at: string | null;
  is_effective: boolean;
  granted_by_user_id: string | null;
  created_at: string;
}

export interface OrgMemberListResponse {
  items: OrgMember[];
  total: number;
}

export interface OrgMemberGrantInput {
  email: string;
  role: GrantableOrganizationRole;
  valid_from?: string;
  valid_until?: string;
}

/** Only the changed fields; the server refuses unknown keys. */
export interface OrgMemberUpdateInput {
  role?: GrantableOrganizationRole;
  valid_until?: string;
  clear_valid_until?: boolean;
}

export interface OrgWorkspace {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  status: string;
  record_scope: string;
  my_workspace_role: string | null;
  scoped_team_ids: string[] | null;
  created_at: string;
}

export interface OrgWorkspaceListResponse {
  items: OrgWorkspace[];
  total: number;
}

export interface OrgWorkspaceCreateInput {
  name: string;
}

export interface WorkspaceMemberInstallInput {
  user_id: string;
  role: DelegatedWorkspaceRole;
}

export interface InstalledWorkspaceMember {
  membership_id: string;
  workspace_id: string;
  user_id: string;
  role: string;
  is_active: boolean;
}

export interface ScopeGrant {
  id: string;
  organization_id: string;
  membership_id: string;
  user_id: string;
  workspace_id: string;
  team_id: string | null;
  granted_by_user_id: string;
  revoked_at: string | null;
  created_at: string;
}

export interface ScopeGrantListResponse {
  items: ScopeGrant[];
  total: number;
}

/** The console never sends `team_id`: no organization route lists a workspace's teams. */
export interface ScopeGrantInput {
  membership_id: string;
  workspace_id: string;
}

export interface WorkspaceAggregate {
  workspace_id: string;
  workspace_name: string;
  team_ids: string[] | null;
  meetings_total: number;
  meetings_by_status: Record<string, number>;
  audio_seconds_total: number;
  action_items_total: number;
}

export interface OrgAggregatesResponse {
  organization_id: string;
  since: string | null;
  until: string | null;
  generated_at: string;
  items: WorkspaceAggregate[];
}
