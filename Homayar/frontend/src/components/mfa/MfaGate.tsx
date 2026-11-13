import { Navigate, Outlet, useLocation } from "react-router";
import { useI18n } from "@/lib/i18n-context";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import { useMfaStatus } from "@/lib/mfa-api";

/**
 * The proactive MFA check (HOM-241 AC2): after the session is authenticated,
 * read GET /auth/mfa once before the app shell renders, so no workspace
 * request fires that the server's MFA gate would refuse.
 *
 * A privileged, unverified session goes to the matching MFA page with the
 * current location as `next`. Everything else (not required, already
 * verified, or the status read failing: network, 5xx, 404 on an old
 * backend) renders the app as before; the reactive path (the 403 listener)
 * still protects it.
 */
export default function MfaGate() {
  const location = useLocation();
  const { lang } = useI18n();
  const copy = MFA_COPY[lang];
  const status = useMfaStatus();

  if (status.isPending) {
    return (
      <div className="session-loading" role="status">
        <span className="session-loading-mark" aria-hidden="true">H</span>
        <span>{copy.common.checking}</span>
      </div>
    );
  }

  if (status.data?.required && !status.data.session_verified) {
    const next = encodeURIComponent(`${location.pathname}${location.search}${location.hash}`);
    const target = status.data.totp_enrolled ? "/mfa/verify" : "/mfa/setup";
    return <Navigate replace to={`${target}?next=${next}`} />;
  }

  return <Outlet />;
}
