/**
 * The QA scorecard requests and hooks (HOM-248). Versions are append-only:
 * the only write is POST /scorecards/{key}/versions, never a PUT, PATCH or
 * DELETE. Kept out of platform-api.ts like ai-policy-api.ts; the query keys
 * still sit under platformQueryKeys.admin().
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type {
  ScorecardDefinition,
  ScorecardListResponse,
  ScorecardPublishInput,
  ScorecardValidateResponse,
  ScorecardVersionListResponse,
  ScorecardVersionResponse,
} from "@/types/scorecards";

export const scorecardQueryKeys = {
  list: () => [...platformQueryKeys.admin(), "scorecards"] as const,
  versions: (key: string) => [...platformQueryKeys.admin(), "scorecards", key, "versions"] as const,
  version: (key: string, version: number) =>
    [...platformQueryKeys.admin(), "scorecards", key, "versions", version] as const,
};

/** A key inside a URL path. A valid key (KEY_PATTERN) is unchanged by the encoding. */
function keyPath(key: string) {
  return `/scorecards/${encodeURIComponent(key)}/versions`;
}

/** The latest version of each scorecard, sorted by key. */
export async function listScorecards(signal?: AbortSignal): Promise<ScorecardListResponse> {
  const path = "/scorecards";
  const response = signal
    ? await api.get<ScorecardListResponse>(path, { signal })
    : await api.get<ScorecardListResponse>(path);
  return response.data;
}

/** Every stored version of one scorecard, newest first. */
export async function listScorecardVersions(
  key: string,
  signal?: AbortSignal,
): Promise<ScorecardVersionListResponse> {
  const path = keyPath(key);
  const response = signal
    ? await api.get<ScorecardVersionListResponse>(path, { signal })
    : await api.get<ScorecardVersionListResponse>(path);
  return response.data;
}

/** One stored version with its definition. */
export async function getScorecardVersion(
  key: string,
  version: number,
  signal?: AbortSignal,
): Promise<ScorecardVersionResponse> {
  const path = `${keyPath(key)}/${version}`;
  const response = signal
    ? await api.get<ScorecardVersionResponse>(path, { signal })
    : await api.get<ScorecardVersionResponse>(path);
  return response.data;
}

/** Appends a version; 409 when `expected_version` is not the current one. */
export async function publishScorecardVersion(
  key: string,
  input: ScorecardPublishInput,
): Promise<ScorecardVersionResponse> {
  const response = await api.post<ScorecardVersionResponse>(keyPath(key), input);
  return response.data;
}

/** Checks a definition without storing it. */
export async function validateScorecard(definition: ScorecardDefinition): Promise<ScorecardValidateResponse> {
  const response = await api.post<ScorecardValidateResponse>("/scorecards/validate", { definition });
  return response.data;
}

export function useScorecards() {
  return useQuery({
    queryKey: scorecardQueryKeys.list(),
    queryFn: ({ signal }) => listScorecards(signal),
  });
}

export function useScorecardVersions(key: string | null) {
  return useQuery({
    queryKey: scorecardQueryKeys.versions(key ?? ""),
    queryFn: ({ signal }) => listScorecardVersions(key ?? "", signal),
    enabled: key !== null,
  });
}

export function useScorecardVersion(key: string | null, version: number | null) {
  return useQuery({
    queryKey: scorecardQueryKeys.version(key ?? "", version ?? 0),
    queryFn: ({ signal }) => getScorecardVersion(key ?? "", version ?? 0, signal),
    enabled: key !== null && version !== null,
  });
}

export function usePublishScorecardVersion() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, input }: { key: string; input: ScorecardPublishInput }) =>
      publishScorecardVersion(key, input),
    onSuccess: async (response) => {
      // The answer is the stored version itself; the list and history need a read.
      queryClient.setQueryData(
        scorecardQueryKeys.version(response.scorecard_key, response.version),
        response,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: scorecardQueryKeys.list(), exact: true }),
        queryClient.invalidateQueries({ queryKey: scorecardQueryKeys.versions(response.scorecard_key), exact: true }),
      ]);
    },
  });
}

export function useValidateScorecard() {
  return useMutation({ mutationFn: validateScorecard });
}
