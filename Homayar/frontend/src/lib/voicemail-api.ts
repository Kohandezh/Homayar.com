/**
 * The voicemail requests and hooks (HOM-252). Kept out of platform-api.ts so
 * sibling tickets land without merge conflicts; the query keys sit under
 * platformQueryKeys.admin().
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type {
  VoicemailAnalytics,
  VoicemailDetail,
  VoicemailListParams,
  VoicemailListResponse,
  VoicemailUpdateInput,
} from "@/types/voicemail";

export const voicemailQueryKeys = {
  all: [...platformQueryKeys.admin(), "voicemails"] as const,
  /** The list key adds the filter params, so each filtered view caches alone. */
  list: (params: VoicemailListParams) => [...voicemailQueryKeys.all, "list", params] as const,
  detail: (id: string) => [...voicemailQueryKeys.all, "detail", id] as const,
  analytics: () => [...voicemailQueryKeys.all, "analytics"] as const,
};

export async function listVoicemails(
  params: VoicemailListParams,
  signal?: AbortSignal,
): Promise<VoicemailListResponse> {
  const response = await api.get<VoicemailListResponse>("/voicemails", { params, signal });
  return response.data;
}

export async function getVoicemail(id: string, signal?: AbortSignal): Promise<VoicemailDetail> {
  const response = await api.get<VoicemailDetail>(`/voicemails/${encodeURIComponent(id)}`, {
    signal,
  });
  return response.data;
}

export async function updateVoicemail(
  id: string,
  patch: VoicemailUpdateInput,
): Promise<VoicemailDetail> {
  const response = await api.patch<VoicemailDetail>(
    `/voicemails/${encodeURIComponent(id)}`,
    patch,
  );
  return response.data;
}

export async function getVoicemailAnalytics(signal?: AbortSignal): Promise<VoicemailAnalytics> {
  const response = await api.get<VoicemailAnalytics>("/voicemails/analytics", { signal });
  return response.data;
}

export function useVoicemails(params: VoicemailListParams) {
  return useQuery({
    queryKey: voicemailQueryKeys.list(params),
    queryFn: ({ signal }) => listVoicemails(params, signal),
  });
}

export function useVoicemail(id: string | null | undefined) {
  return useQuery({
    queryKey: voicemailQueryKeys.detail(id ?? ""),
    queryFn: ({ signal }) => getVoicemail(id ?? "", signal),
    enabled: Boolean(id),
  });
}

export function useUpdateVoicemail() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: VoicemailUpdateInput }) =>
      updateVoicemail(id, patch),
    // Every voicemail key (list pages, the open detail, analytics) follows the
    // new state, so the drawer and the table agree after a write.
    onSettled: () => queryClient.invalidateQueries({ queryKey: voicemailQueryKeys.all }),
  });
}

export function useVoicemailAnalytics() {
  return useQuery({
    queryKey: voicemailQueryKeys.analytics(),
    queryFn: ({ signal }) => getVoicemailAnalytics(signal),
  });
}
