import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";
import { MFA_REQUIRED_EVENT, type MfaRequiredDetail } from "@/lib/api";
import { isMfaPathname } from "@/lib/return-path";

/**
 * The reactive MFA path (HOM-241 AC3): when any API request answers 403
 * `mfa_enrollment_required` or `mfa_required`, the axios interceptor
 * dispatches the window event; this listener, inside the router, navigates
 * to the matching MFA page with the current location as `next`.
 *
 * On an MFA page the event does nothing (no loop); the check reads the path
 * the way the router matches it (decoded, case-insensitive), so no spelling
 * of `/mfa/…` slips through. 401 keeps the refresh behaviour and other 403
 * codes keep the workspace-denied behaviour (src/lib/api.ts).
 */
export default function MfaRequiredListener() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(() => {
    function handleRequired(event: Event) {
      const detail = (event as CustomEvent<MfaRequiredDetail>).detail;
      if (!detail) return;
      if (isMfaPathname(location.pathname)) return;
      const next = encodeURIComponent(`${location.pathname}${location.search}${location.hash}`);
      const target = detail.code === "mfa_enrollment_required" ? "/mfa/setup" : "/mfa/verify";
      navigate(`${target}?next=${next}`, { replace: true });
    }
    window.addEventListener(MFA_REQUIRED_EVENT, handleRequired);
    return () => window.removeEventListener(MFA_REQUIRED_EVENT, handleRequired);
  }, [location, navigate]);

  return null;
}
