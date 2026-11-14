import { useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router";
import { useI18n } from "@/lib/i18n-context";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import { useMfaStatus } from "@/lib/mfa-api";

/**
 * The proactive MFA check (HOM-241 AC2): after the session is authenticated,
 * read GET /auth/mfa before the app shell renders, so no workspace request
 * fires that the server's MFA gate would refuse.
 *
 * Only the first decision of this gate mount blocks. A workspace switch
 * clears the query cache (AuthProvider) while the gate stays mounted; the
 * gate keeps the shell on screen (the sidebar and drawer must not reset), and
 * the next render re-reads the cleared status and still redirects if it says
 * MFA is needed. A genuinely new shell mount (from an MFA page, or a reload)
 * starts with its own blocking first read.
 */
export default function MfaGate() {
  const location = useLocation();
  const { lang } = useI18n();
  const copy = MFA_COPY[lang];
  const status = useMfaStatus();
  const settled = status.data !== undefined || status.isError;

  // Whether this mount has already seen a settled status. It turns on once
  // and stays on, so a later pending re-read keeps the shell. This also
  // covers a first render that is already settled (the status was cached by
  // an MFA page before the shell mounted). It is component state, set during
  // render behind a guard (the pattern React documents), because the lint
  // rules reject module state and reading a ref during render. It resets
  // when the gate unmounts.
  const [decided, setDecided] = useState(false);
  if (settled && !decided) setDecided(true);

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

  if (!decided) {
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
