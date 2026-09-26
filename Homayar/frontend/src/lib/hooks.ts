import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import type { AggregatedIssue, DashboardKpis, CallListItem, CallReport } from "@/types";

// ─── Dashboard ────────────────────────────────────────────

export function useKpis() {
  return useQuery<DashboardKpis>({
    queryKey: ["dashboard", "kpis"],
    queryFn: async () => {
      const res = await api.get("/dashboard/kpis");
      return res.data;
    },
    refetchInterval: 30000,
  });
}

export function useQcTrend(days: number = 7) {
  return useQuery<{ timestamp: string; quality: number }[]>({
    queryKey: ["dashboard", "qc-trend", days],
    queryFn: async () => {
      const res = await api.get("/dashboard/qc-trend", { params: { days } });
      return res.data;
    },
  });
}

export function useSentiment() {
  return useQuery<{ sentiment: string; count: number }[]>({
    queryKey: ["dashboard", "sentiment"],
    queryFn: async () => {
      const res = await api.get("/dashboard/sentiment");
      return res.data;
    },
  });
}

export function useIssues() {
  return useQuery<{ category: string; count: number; critical: number }[]>({
    queryKey: ["dashboard", "issues"],
    queryFn: async () => {
      const res = await api.get("/dashboard/issues");
      return res.data;
    },
  });
}

export function useRecentCalls(limit: number = 20) {
  return useQuery<CallListItem[]>({
    queryKey: ["dashboard", "recent-calls", limit],
    queryFn: async () => {
      const res = await api.get("/dashboard/recent-calls", { params: { limit } });
      return res.data;
    },
  });
}

export function useAggregatedIssues() {
  return useQuery<AggregatedIssue[]>({
    queryKey: ["dashboard", "aggregated-issues"],
    queryFn: async () => {
      const res = await api.get("/dashboard/aggregated-issues");
      return res.data;
    },
  });
}

// ─── Calls ────────────────────────────────────────────────

export function useCalls(status?: string, language?: string) {
  return useQuery<CallListItem[]>({
    queryKey: ["calls", { status, language }],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (status) params.set("status", status);
      if (language) params.set("language", language);
      const res = await api.get(`/calls?${params}`);
      return res.data;
    },
  });
}

export function useCallDetail(callId: string | null) {
  return useQuery<CallReport>({
    queryKey: ["calls", callId],
    queryFn: async () => {
      const res = await api.get(`/calls/${callId}`);
      return res.data;
    },
    enabled: !!callId,
  });
}

export function useAnalyzeCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (callId: string) => {
      const res = await api.post(`/calls/${callId}/analyze`);
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calls"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}

export function useUploadCall() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, agent_id, direction, language }: {
      file: File; agent_id?: string; direction?: string; language?: string;
    }) => {
      const reader = new FileReader();
      const base64 = await new Promise<string>((resolve) => {
        reader.onload = () => {
          const result = reader.result as string;
          resolve(result.split(",")[1]);
        };
        reader.readAsDataURL(file);
      });
      const res = await api.post("/calls/upload", {
        file_base64: base64,
        file_name: file.name,
        agent_id: agent_id || "default-agent",
        direction: direction || "inbound",
        language: language || "fa",
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["calls"] });
    },
  });
}
