/**
 * The ingestions requests and hooks (HOM-251). Kept out of platform-api.ts
 * so the sibling admin tickets land without merge conflicts; the query keys
 * sit under platformQueryKeys.admin().
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type {
  IngestionListParams,
  IngestionListResponse,
  IngestionRecord,
} from "@/types/ingestions";

export const ingestionsQueryKeys = {
  all: [...platformQueryKeys.admin(), "ingestions"] as const,
  /** The list key adds the filter params, so each filtered view caches alone. */
  list: (params: IngestionListParams) => [...ingestionsQueryKeys.all, "list", params] as const,
  detail: (id: string) => [...ingestionsQueryKeys.all, "detail", id] as const,
};

export async function listIngestions(
  params: IngestionListParams,
  signal?: AbortSignal,
): Promise<IngestionListResponse> {
  const response = await api.get<IngestionListResponse>("/ingestions", { params, signal });
  return response.data;
}

export async function getIngestion(id: string, signal?: AbortSignal): Promise<IngestionRecord> {
  const response = await api.get<IngestionRecord>(`/ingestions/${encodeURIComponent(id)}`, { signal });
  return response.data;
}

/** The ledger page; `enabled` is false while the chosen days make no request possible. */
export function useIngestions(params: IngestionListParams, enabled = true) {
  return useQuery({
    queryKey: ingestionsQueryKeys.list(params),
    queryFn: ({ signal }) => listIngestions(params, signal),
    enabled,
    // Paging keeps the current page (and the focused button) while the next
    // one loads, the same as the processing page.
    placeholderData: keepPreviousData,
  });
}

/** One record, fetched when the drawer opens; a null id keeps the hook idle. */
export function useIngestion(id: string | null) {
  return useQuery({
    queryKey: ingestionsQueryKeys.detail(id ?? ""),
    queryFn: ({ signal }) => getIngestion(id ?? "", signal),
    enabled: id !== null,
  });
}
