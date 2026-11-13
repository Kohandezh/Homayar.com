/**
 * The privileged MFA endpoints (HOM-241, ADR 0076). The routes sit outside
 * the server's MFA gate, so a privileged user can always enrol and verify.
 *
 * The enroll, confirm, verify and recovery-codes answers hold one-time
 * secrets. They are plain functions, never React Query hooks, so the values
 * live only in the calling component's state (AC8): nothing secret enters a
 * cache, storage or the URL. Only the status, which holds no secret, is a
 * query.
 */
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";

import api from "@/lib/api";
import type { MfaStatus, MfaVerifyResult, RecoveryCodes, TotpEnrollment } from "@/types/mfa";

export const mfaQueryKeys = {
  /** GET /auth/mfa, shared by the route gate and the account security page. */
  status: () => ["mfa", "status"] as const,
};

export async function getMfaStatus(signal?: AbortSignal): Promise<MfaStatus> {
  const response = signal
    ? await api.get<MfaStatus>("/auth/mfa", { signal })
    : await api.get<MfaStatus>("/auth/mfa");
  return response.data;
}

/**
 * The status query: no secret, so the console's normal caching applies. The
 * status is read once per session; it is never refetched on a remount (the
 * route gate remounts while a lazy page loads, and a read that errored on an
 * old backend would otherwise refetch in a loop). "Try again" and the
 * post-confirm re-read call `refetch` / `refreshMfaStatus` on purpose.
 */
export function useMfaStatus() {
  return useQuery({
    queryKey: mfaQueryKeys.status(),
    queryFn: ({ signal }) => getMfaStatus(signal),
    staleTime: Infinity,
    retry: false,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

/**
 * After a successful confirm or verify, re-read the status into the shared
 * cache, so the route gate (AC2) does not send the user straight back to an
 * MFA page. A failed re-read just marks the query stale: the gate then reads
 * it again on its next mount.
 */
export async function refreshMfaStatus(queryClient: QueryClient): Promise<void> {
  try {
    queryClient.setQueryData(mfaQueryKeys.status(), await getMfaStatus());
  } catch {
    queryClient.invalidateQueries({ queryKey: mfaQueryKeys.status() });
  }
}

/** Convenience wrapper for components that already hold a query client. */
export function useRefreshMfaStatus() {
  const queryClient = useQueryClient();
  return () => refreshMfaStatus(queryClient);
}

/** POST /auth/mfa/totp/enroll: the pending secret, returned once. */
export async function enrollTotp(): Promise<TotpEnrollment> {
  const response = await api.post<TotpEnrollment>("/auth/mfa/totp/enroll");
  return response.data;
}

/** POST /auth/mfa/totp/confirm: makes the pending secret the confirmed factor. */
export async function confirmTotp(code: string): Promise<RecoveryCodes> {
  const response = await api.post<RecoveryCodes>("/auth/mfa/totp/confirm", { code });
  return response.data;
}

/** POST /auth/mfa/verify: a TOTP code or a recovery code, as typed (only trimmed). */
export async function verifyMfa(code: string): Promise<MfaVerifyResult> {
  const response = await api.post<MfaVerifyResult>("/auth/mfa/verify", { code });
  return response.data;
}

/** POST /auth/mfa/recovery-codes: replaces the whole set (verified session only). */
export async function regenerateRecoveryCodes(): Promise<RecoveryCodes> {
  const response = await api.post<RecoveryCodes>("/auth/mfa/recovery-codes");
  return response.data;
}
