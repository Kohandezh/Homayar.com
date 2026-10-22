/**
 * The voice capacity requests and hooks (HOM-194). Kept out of
 * platform-api.ts so the sibling admin pages can land without merge
 * conflicts; the query key still sits under platformQueryKeys.admin().
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type { VoiceCapacityResponse, VoiceNodeDrainResponse, VoiceRecoverResponse } from "@/types/voice";

/** One cache entry for the overview card and the voice operations page. */
export const VOICE_CAPACITY_QUERY_KEY = [...platformQueryKeys.admin(), "voice-capacity"] as const;

/** Both the card and the page read the capacity again this often. */
export const VOICE_REFRESH_MS = 15_000;

export async function getVoiceCapacity(): Promise<VoiceCapacityResponse> {
  const response = await api.get<VoiceCapacityResponse>("/voice/capacity");
  return response.data;
}

/** Drains a node (`draining: true`) or lets it take calls again (`false`). */
export async function setNodeDraining({
  nodeId,
  draining,
}: {
  nodeId: string;
  draining: boolean;
}): Promise<VoiceNodeDrainResponse> {
  const response = await api.post<VoiceNodeDrainResponse>(
    `/voice/nodes/${encodeURIComponent(nodeId)}/drain`,
    { draining },
  );
  return response.data;
}

/** Runs the recovery sweep once (the worker also runs it on a schedule). */
export async function recoverVoice(): Promise<VoiceRecoverResponse> {
  const response = await api.post<VoiceRecoverResponse>("/voice/recover");
  return response.data;
}

export function useVoiceCapacity() {
  return useQuery({
    queryKey: VOICE_CAPACITY_QUERY_KEY,
    queryFn: getVoiceCapacity,
    refetchInterval: VOICE_REFRESH_MS,
  });
}

export function useSetNodeDraining() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: setNodeDraining,
    onSuccess: () => {
      // Not awaited: the dialog closes now, and the table updates when the read answers.
      void queryClient.invalidateQueries({ queryKey: VOICE_CAPACITY_QUERY_KEY });
    },
  });
}

export function useRecoverVoice() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: recoverVoice,
    onSuccess: () => {
      // Not awaited: the dialog closes now, and the page updates when the read answers.
      void queryClient.invalidateQueries({ queryKey: VOICE_CAPACITY_QUERY_KEY });
    },
  });
}
