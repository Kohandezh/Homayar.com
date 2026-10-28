/**
 * The organization admin requests and hooks (HOM-193). Kept out of
 * platform-api.ts, like ai-policy-api.ts, so the console tickets that run at
 * the same time do not conflict. Every key starts with `["organizations"]`,
 * so one invalidation re-checks the whole area.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { apiErrorInfo } from "@/lib/api-errors";
import type {
  InstalledWorkspaceMember,
  OrgAggregatesResponse,
  OrgMember,
  OrgMemberGrantInput,
  OrgMemberListResponse,
  OrgMemberUpdateInput,
  OrgWorkspace,
  OrgWorkspaceCreateInput,
  OrgWorkspaceListResponse,
  Organization,
  OrganizationListResponse,
  ScopeGrant,
  ScopeGrantInput,
  ScopeGrantListResponse,
  WorkspaceMemberInstallInput,
} from "@/types/organizations";

export const orgQueryKeys = {
  all: ["organizations"] as const,
  list: () => ["organizations", "list"] as const,
  detail: (organizationId: string) => ["organizations", "detail", organizationId] as const,
  members: (organizationId: string) => ["organizations", "detail", organizationId, "members"] as const,
  workspaces: (organizationId: string) => ["organizations", "detail", organizationId, "workspaces"] as const,
  scopeGrants: (organizationId: string) => ["organizations", "detail", organizationId, "scope-grants"] as const,
  aggregatesRoot: (organizationId: string) => ["organizations", "detail", organizationId, "aggregates"] as const,
  aggregates: (organizationId: string, since: string | null) =>
    ["organizations", "detail", organizationId, "aggregates", { since }] as const,
};

/**
 * True for the 404 the org routes answer when the caller is not (or no
 * longer) a member: the backend never tells "no such org" apart from
 * "membership revoked or expired".
 */
export function isOrganizationGone(error: unknown): boolean {
  const info = apiErrorInfo(error);
  return info.status === 404 && info.code === "organization_not_found";
}

/** `/organizations/<id>/<rest>`, every id encoded. */
function orgPath(organizationId: string, ...rest: string[]) {
  return ["/organizations", ...[organizationId, ...rest].map(encodeURIComponent)].join("/");
}

export async function listOrganizations(signal?: AbortSignal): Promise<OrganizationListResponse> {
  const response = await api.get<OrganizationListResponse>("/organizations", { signal });
  return response.data;
}

export async function getOrganization(organizationId: string, signal?: AbortSignal): Promise<Organization> {
  const response = await api.get<Organization>(orgPath(organizationId), { signal });
  return response.data;
}

export async function listOrgMembers(organizationId: string, signal?: AbortSignal): Promise<OrgMemberListResponse> {
  const response = await api.get<OrgMemberListResponse>(orgPath(organizationId, "members"), { signal });
  return response.data;
}

export async function listOrgWorkspaces(organizationId: string, signal?: AbortSignal): Promise<OrgWorkspaceListResponse> {
  const response = await api.get<OrgWorkspaceListResponse>(orgPath(organizationId, "workspaces"), { signal });
  return response.data;
}

export async function listScopeGrants(organizationId: string, signal?: AbortSignal): Promise<ScopeGrantListResponse> {
  const response = await api.get<ScopeGrantListResponse>(orgPath(organizationId, "scope-grants"), { signal });
  return response.data;
}

/**
 * `since` null asks for the server default: the 4 closed weeks before the
 * current week. No `since` and no `until` is sent then. The window really
 * used is `coverage_window` in the answer (contract 1.7.0).
 */
export async function getOrgAggregates(
  organizationId: string,
  since: string | null,
  signal?: AbortSignal,
): Promise<OrgAggregatesResponse> {
  const path = orgPath(organizationId, "aggregates");
  const response = since
    ? await api.get<OrgAggregatesResponse>(path, { params: { since }, signal })
    : await api.get<OrgAggregatesResponse>(path, { signal });
  return response.data;
}

export async function grantOrgMember(organizationId: string, input: OrgMemberGrantInput): Promise<OrgMember> {
  const response = await api.post<OrgMember>(orgPath(organizationId, "members"), input);
  return response.data;
}

export async function updateOrgMember(
  organizationId: string,
  membershipId: string,
  patch: OrgMemberUpdateInput,
): Promise<OrgMember> {
  const response = await api.patch<OrgMember>(orgPath(organizationId, "members", membershipId), patch);
  return response.data;
}

export async function revokeOrgMember(organizationId: string, membershipId: string): Promise<void> {
  await api.delete(orgPath(organizationId, "members", membershipId));
}

export async function createOrgWorkspace(organizationId: string, input: OrgWorkspaceCreateInput): Promise<OrgWorkspace> {
  const response = await api.post<OrgWorkspace>(orgPath(organizationId, "workspaces"), input);
  return response.data;
}

export async function installWorkspaceMember(
  organizationId: string,
  workspaceId: string,
  input: WorkspaceMemberInstallInput,
): Promise<InstalledWorkspaceMember> {
  const response = await api.post<InstalledWorkspaceMember>(
    orgPath(organizationId, "workspaces", workspaceId, "members"),
    input,
  );
  return response.data;
}

export async function createScopeGrant(organizationId: string, input: ScopeGrantInput): Promise<ScopeGrant> {
  const response = await api.post<ScopeGrant>(orgPath(organizationId, "scope-grants"), input);
  return response.data;
}

export async function revokeScopeGrant(organizationId: string, grantId: string): Promise<void> {
  await api.delete(orgPath(organizationId, "scope-grants", grantId));
}

/** The switcher's list. Hidden on error, so one retry is enough. */
export function useOrganizations() {
  return useQuery({
    queryKey: orgQueryKeys.list(),
    queryFn: ({ signal }) => listOrganizations(signal),
    retry: false,
  });
}

/** The guard's read: the role in its answer is the only source of access. */
export function useOrganization(organizationId: string) {
  return useQuery({
    queryKey: orgQueryKeys.detail(organizationId),
    queryFn: ({ signal }) => getOrganization(organizationId, signal),
    // A 404 is an answer (no access), not a hiccup to retry.
    retry: false,
  });
}

export function useOrgMembers(organizationId: string, options: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: orgQueryKeys.members(organizationId),
    queryFn: ({ signal }) => listOrgMembers(organizationId, signal),
    enabled: options.enabled ?? true,
    retry: false,
  });
}

export function useOrgWorkspaces(organizationId: string) {
  return useQuery({
    queryKey: orgQueryKeys.workspaces(organizationId),
    queryFn: ({ signal }) => listOrgWorkspaces(organizationId, signal),
    retry: false,
  });
}

export function useScopeGrants(organizationId: string) {
  return useQuery({
    queryKey: orgQueryKeys.scopeGrants(organizationId),
    queryFn: ({ signal }) => listScopeGrants(organizationId, signal),
    retry: false,
  });
}

export function useOrgAggregates(organizationId: string, since: string | null) {
  return useQuery({
    queryKey: orgQueryKeys.aggregates(organizationId, since),
    queryFn: ({ signal }) => getOrgAggregates(organizationId, since, signal),
    retry: false,
  });
}

export function useGrantOrgMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: OrgMemberGrantInput) => grantOrgMember(organizationId, input),
    onSettled: () => queryClient.invalidateQueries({ queryKey: orgQueryKeys.members(organizationId) }),
  });
}

export function useUpdateOrgMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ membershipId, patch }: { membershipId: string; patch: OrgMemberUpdateInput }) =>
      updateOrgMember(organizationId, membershipId, patch),
    onSettled: () => queryClient.invalidateQueries({ queryKey: orgQueryKeys.members(organizationId) }),
  });
}

export function useRevokeOrgMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (membershipId: string) => revokeOrgMember(organizationId, membershipId),
    // A revoked analyst or executive also loses its scope grants' effect.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: orgQueryKeys.members(organizationId) }),
        queryClient.invalidateQueries({ queryKey: orgQueryKeys.scopeGrants(organizationId) }),
      ]),
  });
}

export function useCreateOrgWorkspace(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: OrgWorkspaceCreateInput) => createOrgWorkspace(organizationId, input),
    // The overview counts workspaces and lists their totals, so it refreshes too.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: orgQueryKeys.workspaces(organizationId) }),
        queryClient.invalidateQueries({ queryKey: orgQueryKeys.aggregatesRoot(organizationId) }),
      ]),
  });
}

export function useInstallWorkspaceMember(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ workspaceId, input }: { workspaceId: string; input: WorkspaceMemberInstallInput }) =>
      installWorkspaceMember(organizationId, workspaceId, input),
    onSettled: () => queryClient.invalidateQueries({ queryKey: orgQueryKeys.workspaces(organizationId) }),
  });
}

export function useCreateScopeGrant(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: ScopeGrantInput) => createScopeGrant(organizationId, input),
    onSettled: () => queryClient.invalidateQueries({ queryKey: orgQueryKeys.scopeGrants(organizationId) }),
  });
}

export function useRevokeScopeGrant(organizationId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (grantId: string) => revokeScopeGrant(organizationId, grantId),
    onSettled: () => queryClient.invalidateQueries({ queryKey: orgQueryKeys.scopeGrants(organizationId) }),
  });
}
