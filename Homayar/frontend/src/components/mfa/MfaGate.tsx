import { useEffect } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useI18n } from "@/lib/i18n-context";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import { mfaQueryKeys, useMfaStatus } from "@/lib/mfa-api";

/**
 * Whether an app shell mount has already seen a settled MFA status. Module
 * state, written only from effects (see MfaGate): it must survive the query
 * cache being cleared while the gate stays mounted, and reset when the shell
 * unmounts (an MFA page, signing out).
 */
let gateDecided = false;

/**
 * The proactive MFA check (HOM-241 AC2): after the session is authenticated,
 * read GET /auth/mfa before the app shell renders, so no workspace request
 * fires that the server's MFA gate would refuse.
 *
 * Only the first decision of an app-shell mount blocks. A workspace switch
 * clears the query cache (AuthProvider) while the gate stays mounted; the
 * gate keeps the shell on screen (the sidebar and drawer must not reset),
 * re-reads the cleared status, and still redirects if it says MFA is needed.
 * A genuinely new shell mount (from an MFA page, or a reload) starts with
 * its own blocking first read.
 */
export default function MfaGate() {
  const location = useLocation();
  const { lang } = useI18n();
  const copy = MFA_COPY[lang];
  const status = useMfaStatus();
  const { refetch } = status;
  const queryClient = useQueryClient();
  const settled = status.data !== undefined || status.isError;

  useEffect(() => {
    if (settled) gateDecided = true;
  }, [settled]);

  // Leaving the app shell resets the decision for the next mount.
  useEffect(() => () => {
    gateDecided = false;
  }, []);

  // React Query keeps the observer's last result when the cache is cleared,
  // so nothing would ever re-read the status after a workspace switch: watch
  // for the removal and fetch again, so a status that now needs MFA still
  // redirects.
  useEffect(() => {
    const cache = queryClient.getQueryCache();
    return cache.subscribe((event) => {
      if (event.type !== "removed") return;
      if (event.query.queryKey.join("/") !== mfaQueryKeys.status().join("/")) return;
      void refetch();
    });
  }, [queryClient, refetch]);

  if (status.data) {
    if (status.data.required && !status.data.session_verified) {
      const next = encodeURIComponent(`${location.pathname}${location.search}${location.hash}`);
      const target = status.data.totp_enrolled ? "/mfa/verify" : "/mfa/setup";
      return <Navigate replace to={`${target}?next=${next}`} />;
    }
    return <Outlet />;
  }

  if (status.isError) {
    // The API did not answer (network, 5xx, 404 on an old backend): the
    // session may still be valid, so this is neither a redirect nor an error
    // screen; the reactive path (the 403 listener) still protects the app.
    return <Outlet />;
  }

  if (!gateDecided) {
    return (
      <div className="session-loading" role="status">
        <span className="session-loading-mark" aria-hidden="true">H</span>
        <span>{copy.common.checking}</span>
      </div>
    );
  }

  // A settled decision exists and a re-read is running: keep the shell.
  return <Outlet />;
}
