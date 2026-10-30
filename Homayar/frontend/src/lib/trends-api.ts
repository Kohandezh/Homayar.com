/**
 * The issue trends requests and hooks (HOM-237): GET /trends/issues and the
 * conversations behind one cell, GET /trends/conversations. The query keys
 * carry every parameter, so each filtered view caches alone.
 */
import { useQuery } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type {
  IssueTrendParams,
  IssueTrendResponse,
  TrendLinkParams,
  TrendLinkResponse,
} from "@/types/trends";

export const trendsQueryKeys = {
  all: [...platformQueryKeys.all, "trends"] as const,
  issues: (params: IssueTrendParams) => [...trendsQueryKeys.all, "issues", params] as const,
  conversations: (params: TrendLinkParams) => [...trendsQueryKeys.all, "conversations", params] as const,
};

export async function getIssueTrends(params: IssueTrendParams): Promise<IssueTrendResponse> {
  const response = await api.get<IssueTrendResponse>("/trends/issues", { params });
  return response.data;
}

export async function getTrendConversations(params: TrendLinkParams): Promise<TrendLinkResponse> {
  const response = await api.get<TrendLinkResponse>("/trends/conversations", { params });
  return response.data;
}

/**
 * The trends of the filters on screen. No placeholder data: numbers of other
 * filters must never stand in while the new ones load.
 */
export function useIssueTrends(params: IssueTrendParams) {
  return useQuery({
    queryKey: trendsQueryKeys.issues(params),
    queryFn: () => getIssueTrends(params),
  });
}

/** One cell's conversations; a null query (no cell opened) keeps the hook idle. */
export function useTrendConversations(params: TrendLinkParams | null) {
  return useQuery({
    queryKey: params ? trendsQueryKeys.conversations(params) : [...trendsQueryKeys.all, "conversations", "idle"],
    queryFn: () => getTrendConversations(params as TrendLinkParams),
    enabled: params !== null,
    // A withheld cell answers 403 trend_links_withheld; asking again changes nothing.
    retry: false,
  });
}
