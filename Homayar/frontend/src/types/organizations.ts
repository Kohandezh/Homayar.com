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

/** The half-open window a count covers: `from <= received_at < to`, ISO UTC. */
export interface CoverageWindow {
  from: string;
  to: string;
  basis: "ingestion_received_at";
}

export interface CoverageCounter {
  definition: string;
  numerator_label: string;
  denominator_label: string;
  numerator: number | null;
  denominator: number;
  ratio: number | null;
  status: "computed" | "not_computable";
  reason: string | null;
  buckets: Record<string, number>;
}

export interface TrendCoverage {
  version: "coverage/1";
  window: { from: string; to: string; basis: string };
  basis: "workspace" | "trend_population";
  processing: CoverageCounter;
  evaluation: CoverageCounter;
}

export type MissingDataIndicator =
  | "ingestion_incomplete"
  | "processing_incomplete"
  | "evaluation_incomplete"
  | "review_incomplete"
  | "ingestion_unavailable"
  | "processing_unavailable"
  | "evaluation_unavailable"
  | "review_unavailable"
  | "scorecard_unavailable"
  | "empty_population"
  | "missing_values"
  | "pending_review"
  | "insufficient_sample"
  | "source_unavailable";

/**
 * Whether the counts of one workspace may be shown (contract 1.7.0, the
 * HOM-82 small-group rule). Only `released` carries counts.
 */
export interface AggregateDisclosure {
  status: "released" | "masked" | "withheld";
  reason: "small_group" | "complementary_disclosure" | null;
  kind: "rate" | "agent" | null;
  masked: ("points" | "window")[];
}

/** One workspace. The four counts are null when the workspace is withheld. */
export interface WorkspaceAggregate {
  workspace_id: string;
  workspace_name: string;
  team_ids: string[] | null;
  meetings_total: number | null;
  meetings_by_status: Record<string, number> | null;
  audio_seconds_total: number | null;
  action_items_total: number | null;
  coverage: TrendCoverage | null;
  scorecard: {
    status: "not_available";
    version: string | null;
    digest: string | null;
    reason: "scorecard_results_unavailable" | null;
  };
  missing_data: { indicators: MissingDataIndicator[] } | null;
  disclosure: AggregateDisclosure;
}

export interface AggregateDisclosurePolicy {
  version: string;
  /** Fewer agents than this in any week of the window withholds the workspace. */
  min_cohort_agents: number;
  grid: {
    unit: "week";
    timezone: string;
    week_starts_on: "saturday";
    closed_weeks_only: boolean;
  };
}

export interface OrgAggregatesResponse {
  organization_id: string;
  /** Echo of the request; `coverage_window` is the window really used. */
  since: string | null;
  until: string | null;
  generated_at: string;
  items: WorkspaceAggregate[];
  disclosure_policy: AggregateDisclosurePolicy;
  coverage_window: CoverageWindow;
}
