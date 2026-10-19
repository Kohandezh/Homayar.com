/**
 * The locations requests and hooks (HOM-191). Kept out of platform-api.ts so
 * the sibling admin tickets land without merge conflicts; the query keys sit
 * under platformQueryKeys.admin().
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import type {
  LocationCreateInput,
  LocationListParams,
  LocationListResponse,
  LocationSaveResponse,
  LocationUpdateInput,
} from "@/types/locations";

export const locationsQueryKeys = {
  all: [...platformQueryKeys.admin(), "locations"] as const,
  /** The list key adds the filter params, so each filtered view caches alone. */
  list: (params: LocationListParams) => [...locationsQueryKeys.all, params] as const,
};

export async function listLocations(
  params: LocationListParams,
  signal?: AbortSignal,
): Promise<LocationListResponse> {
  const response = await api.get<LocationListResponse>("/locations", { params, signal });
  return response.data;
}

export async function createLocation(input: LocationCreateInput): Promise<LocationSaveResponse> {
  const response = await api.post<LocationSaveResponse>("/locations", input);
  return response.data;
}

export async function updateLocation(id: string, patch: LocationUpdateInput): Promise<LocationSaveResponse> {
  const response = await api.patch<LocationSaveResponse>(`/locations/${encodeURIComponent(id)}`, patch);
  return response.data;
}

export async function deleteLocation(id: string): Promise<void> {
  await api.delete(`/locations/${encodeURIComponent(id)}`);
}

export function useLocations(params: LocationListParams) {
  return useQuery({
    queryKey: locationsQueryKeys.list(params),
    queryFn: ({ signal }) => listLocations(params, signal),
  });
}

export function useCreateLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createLocation,
    onSettled: () => queryClient.invalidateQueries({ queryKey: locationsQueryKeys.all }),
  });
}

export function useUpdateLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: LocationUpdateInput }) => updateLocation(id, patch),
    onSettled: () => queryClient.invalidateQueries({ queryKey: locationsQueryKeys.all }),
  });
}

export function useDeleteLocation() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteLocation(id),
    onSettled: () => queryClient.invalidateQueries({ queryKey: locationsQueryKeys.all }),
  });
}
