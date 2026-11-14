import { useRef, useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { ShieldCheck } from "lucide-react";
import { apiErrorInfo } from "@/lib/api-errors";
import { describeApiError } from "@/lib/error-messages";
import { formatNumber } from "@/lib/format";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { safeNextPath } from "@/lib/return-path";
import { confirmTotp, enrollTotp, markMfaSessionVerified } from "@/lib/mfa-api";
import { AuthCard, FormAlert, SubmitButton } from "@/components/auth/AuthUi";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import RecoveryCodesView from "@/components/mfa/RecoveryCodesView";
import { useCopyToClipboard } from "@/components/mfa/copy-to-clipboard";
import { mfaFormError, type MfaFormError } from "@/components/mfa/mfa-errors";
import type { TotpEnrollment } from "@/types/mfa";

/** The base32 secret in groups of four characters, as shown on screen. */
function groupSecret(secret: string): string {
  return secret.replace(/(.{4})(?=.)/g, "$1 ");
}

/**
 * The enrolment page (HOM-241 AC4). It renders inside AuthLayout, outside
 * AppLayout and WorkspaceGate, so no workspace request fires that the
 * server's MFA gate would refuse.
 *
 * The pending secret and otpauth URI live only in this component's state
 * (AC8): nothing secret is cached, stored or logged, the QR is drawn in the
 * browser, and on success both leave the screen with the state.
 */
export default function MfaSetupPage() {
  const { lang } = useI18n();
  const copy = MFA_COPY[lang];
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));

  const [enrollment, setEnrollment] = useState<TotpEnrollment | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<MfaFormError | null>(null);
  const [busy, setBusy] = useState(false);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const codeInput = useRef<HTMLInputElement>(null);
  const { copied, failed, copy: copyText } = useCopyToClipboard();

  function errorText(formError: MfaFormError): string {
    switch (formError.kind) {
      case "empty": return copy.errors.emptyCode;
      case "code_invalid": return copy.errors.codeInvalid;
      case "rate_limited": return copy.errors.rateLimited(formatNumber(formError.minutes, lang));
      case "rate_limited_later": return copy.errors.rateLimitedLater;
      case "reauthentication": return copy.errors.reauthentication;
      case "expired": return copy.errors.expired;
      case "unavailable": return copy.errors.unavailable;
      case "generic": return formError.text;
    }
  }

  async function handleSignOut() {
    setSignOutError(null);
    try {
      await signOut();
    } catch (error) {
      // The session is still live: stay here and say so, instead of
      // bouncing the user back from /login (the Sidebar's own text).
      setSignOutError(describeApiError(error, { lang, fallback: copy.common.signOutFailed }).text);
      return;
    }
    navigate("/login", { replace: true });
  }

  /** Start (or, after a 409, start again) the enrolment: one POST per click. */
  async function startSetup() {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      setEnrollment(await enrollTotp());
      setCode("");
      codeInput.current?.focus();
    } catch (caught) {
      if (apiErrorInfo(caught).code === "mfa_required") {
        navigate(`/mfa/verify?next=${encodeURIComponent(next)}`, { replace: true });
        return;
      }
      setError(mfaFormError(caught, lang, copy.errors.generic));
    } finally {
      setBusy(false);
    }
  }

  async function submitCode(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !enrollment) return;
    const trimmed = code.trim();
    if (!trimmed) {
      setError({ kind: "empty" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { recovery_codes } = await confirmTotp(trimmed);
      // The secret and the typed code leave the screen with the state (AC8).
      setEnrollment(null);
      setCode("");
      // The server accepted the code: the cached status is verified at once,
      // and a failing re-read cannot bounce the user back (AC5).
      markMfaSessionVerified(queryClient, { totpEnrolled: true });
      setRecoveryCodes(recovery_codes);
    } catch (caught) {
      if (apiErrorInfo(caught).code === "mfa_required") {
        navigate(`/mfa/verify?next=${encodeURIComponent(next)}`, { replace: true });
        return;
      }
      const mapped = mfaFormError(caught, lang, copy.errors.generic);
      setError(mapped);
      if (mapped.kind === "code_invalid") {
        // The field keeps focus for the next attempt (AC4).
        requestAnimationFrame(() => codeInput.current?.focus());
      }
    } finally {
      setBusy(false);
    }
  }

  const signOutButton = (
    <div className="flex flex-col items-start gap-2">
      <button type="button" className="auth-text-button" onClick={() => void handleSignOut()}>
        {copy.common.signOut}
      </button>
      {signOutError && <p className="m-0 platform-form-error" role="alert">{signOutError}</p>}
    </div>
  );

  if (recoveryCodes) {
    return (
      <AuthCard title={copy.setup.title} description="" footer={signOutButton}>
        <RecoveryCodesView codes={recoveryCodes} onContinue={() => navigate(next, { replace: true })} />
      </AuthCard>
    );
  }

  if (!enrollment) {
    return (
      <AuthCard title={copy.setup.title} description={copy.setup.intro} footer={signOutButton}>
        {error && <FormAlert>{errorText(error)}</FormAlert>}
        {error?.kind === "reauthentication" && (
          <button type="button" className="auth-submit" onClick={() => void handleSignOut()}>
            {copy.errors.signInAgain}
          </button>
        )}
        {error?.kind !== "reauthentication" && (
          <SubmitButton type="button" busy={busy} onClick={() => void startSetup()}>
            {copy.setup.start}
          </SubmitButton>
        )}
      </AuthCard>
    );
  }

  return (
    <AuthCard title={copy.setup.title} description={copy.setup.intro} footer={signOutButton}>
      {/* The QR is drawn in the browser from the otpauth URI; no network (AC4).
          The wrapper is explicit left to right, so the code stays upright in
          the right-to-left page (AC9). */}
      <div className="flex justify-center" dir="ltr">
        <QRCodeSVG
          value={enrollment.otpauth_uri}
          size={176}
          level="M"
          marginSize={2}
          role="img"
          aria-label={copy.setup.qrLabel}
        />
      </div>

      <div className="mt-4">
        <p className="m-0 mb-1 text-sm font-medium">{copy.setup.setupKey}</p>
        {/* The key is left-to-right data in both languages (AC9). */}
        <p dir="ltr" className="m-0 font-mono text-sm leading-relaxed">{groupSecret(enrollment.secret)}</p>
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <button type="button" className="auth-text-button" onClick={() => copyText(enrollment.secret)}>
            {copied ? copy.setup.copied : copy.setup.copyKey}
          </button>
          {failed && <p className="m-0 platform-form-error" role="alert">{copy.setup.copyFailed}</p>}
        </div>
      </div>

      <form className="auth-form mt-4" onSubmit={submitCode} noValidate>
        <div className="auth-field">
          <label htmlFor="mfa-setup-code">{copy.setup.codeField}</label>
          <div className="auth-input-wrap">
            <ShieldCheck aria-hidden="true" />
            <input
              id="mfa-setup-code"
              ref={codeInput}
              className="auth-input"
              dir="ltr"
              inputMode="numeric"
              autoComplete="one-time-code"
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </div>
        </div>

        {error && <FormAlert>{errorText(error)}</FormAlert>}

        <SubmitButton busy={busy}>{copy.setup.confirm}</SubmitButton>

        {error?.kind === "expired" && (
          <button type="button" className="auth-submit" onClick={() => void startSetup()}>
            {copy.setup.startAgain}
          </button>
        )}
        {error?.kind === "reauthentication" && (
          <button type="button" className="auth-submit" onClick={() => void handleSignOut()}>
            {copy.errors.signInAgain}
          </button>
        )}
      </form>
    </AuthCard>
  );
}
