/**
 * The system health request and hook (HOM-232, endpoint HOM-227). Kept out
 * of platform-api.ts so the sibling admin pages land without merge
 * conflicts; the query key still sits under platformQueryKeys.admin().
 */
import { useQuery } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type { SystemHealthResponse } from "@/types/system-health";

export const SYSTEM_HEALTH_QUERY_KEY = [...platformQueryKeys.admin(), "system-health"] as const;

/** The page reads the health again this often (P10). */
export const SYSTEM_HEALTH_REFRESH_MS = 30_000;

export async function getSystemHealth(): Promise<SystemHealthResponse> {
  const response = await api.get<SystemHealthResponse>("/admin/system-health");
  return response.data;
}

export function useSystemHealth() {
  return useQuery({
    queryKey: SYSTEM_HEALTH_QUERY_KEY,
    queryFn: getSystemHealth,
    refetchInterval: SYSTEM_HEALTH_REFRESH_MS,
  });
}
