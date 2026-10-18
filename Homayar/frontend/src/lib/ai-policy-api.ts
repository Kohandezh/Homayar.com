/**
 * The AI policy requests and hooks (HOM-188). Kept out of platform-api.ts so
 * the sibling admin pages (HOM-181, HOM-182, HOM-187) can land without merge
 * conflicts; the query keys still sit under platformQueryKeys.admin().
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type {
  AiPolicyResponse,
  AiPolicyUpdateInput,
  AiPolicyUpdateResponse,
  AiPolicyVersionListResponse,
} from "@/types/ai-policy";

export const aiPolicyQueryKeys = {
  current: () => [...platformQueryKeys.admin(), "ai-policy"] as const,
  versions: () => [...platformQueryKeys.admin(), "ai-policy", "versions"] as const,
};

/** The policy the workspace runs under now (owner and admin). */
export async function getAiPolicy(signal?: AbortSignal): Promise<AiPolicyResponse> {
  const path = "/admin/ai-policy";
  const response = signal
    ? await api.get<AiPolicyResponse>(path, { signal })
    : await api.get<AiPolicyResponse>(path);
  return response.data;
}

/** Stored versions, newest first, plus the presets an owner can start from. */
export async function getAiPolicyVersions(
  signal?: AbortSignal,
): Promise<AiPolicyVersionListResponse> {
  const path = "/admin/ai-policy/versions";
  const response = signal
    ? await api.get<AiPolicyVersionListResponse>(path, { signal })
    : await api.get<AiPolicyVersionListResponse>(path);
  return response.data;
}

/** Appends a new version; the server answers 409 when expected_version is stale. */
export async function updateAiPolicy(input: AiPolicyUpdateInput): Promise<AiPolicyUpdateResponse> {
  const response = await api.put<AiPolicyUpdateResponse>("/admin/ai-policy", input);
  return response.data;
}

export function useAiPolicy() {
  return useQuery({
    queryKey: aiPolicyQueryKeys.current(),
    queryFn: ({ signal }) => getAiPolicy(signal),
  });
}

export function useAiPolicyVersions() {
  return useQuery({
    queryKey: aiPolicyQueryKeys.versions(),
    queryFn: ({ signal }) => getAiPolicyVersions(signal),
  });
}

export function useUpdateAiPolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: updateAiPolicy,
    onSuccess: async (response) => {
      // The answer already holds the fresh current policy; the history needs a read.
      queryClient.setQueryData(aiPolicyQueryKeys.current(), response.current);
      await queryClient.invalidateQueries({ queryKey: aiPolicyQueryKeys.versions() });
    },
  });
}
