import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, ShieldCheck } from "lucide-react";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatNumber } from "@/lib/format";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { safeNextPath } from "@/lib/return-path";
import { refreshMfaStatus, verifyMfa } from "@/lib/mfa-api";
import { AuthCard, FormAlert, SubmitButton } from "@/components/auth/AuthUi";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import { mfaFormError, type MfaFormError } from "@/components/mfa/mfa-errors";

type Mode = "totp" | "recovery";

/**
 * The session verification page (HOM-241 AC5): a TOTP code or a recovery
 * code for a user who already has a factor. Renders inside AuthLayout,
 * outside AppLayout and WorkspaceGate. Nothing the user types is stored,
 * logged or cached (AC8).
 */
export default function MfaVerifyPage() {
  const { lang } = useI18n();
  const copy = MFA_COPY[lang];
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams] = useSearchParams();
  const next = safeNextPath(searchParams.get("next"));

  const [mode, setMode] = useState<Mode>("totp");
  const [code, setCode] = useState("");
  const [error, setError] = useState<MfaFormError | null>(null);
  const [busy, setBusy] = useState(false);
  const [recoveryRemaining, setRecoveryRemaining] = useState<number | null>(null);
  const codeInput = useRef<HTMLInputElement>(null);

  function errorText(formError: MfaFormError): string {
    switch (formError.kind) {
      case "empty": return mode === "recovery" ? copy.errors.emptyRecovery : copy.errors.emptyCode;
      case "code_invalid": return mode === "recovery" ? copy.errors.recoveryInvalid : copy.errors.codeInvalid;
      case "rate_limited": return copy.errors.rateLimited(formatNumber(formError.minutes, lang));
      case "rate_limited_later": return copy.errors.rateLimitedLater;
      case "reauthentication": return copy.errors.reauthentication;
      case "expired": return copy.errors.expired;
      case "unavailable": return copy.errors.unavailable;
      case "generic": return formError.text;
    }
  }

  /** Switching clears the field and any error (AC5). */
  function switchMode(target: Mode) {
    if (target === mode) return;
    setMode(target);
    setCode("");
    setError(null);
  }

  async function handleSignOut() {
    try {
      await signOut();
    } catch {
      // A failed logout still leaves this page; the session ends server-side.
    }
    navigate("/login", { replace: true });
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const trimmed = code.trim();
    if (!trimmed) {
      setError({ kind: "empty" });
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await verifyMfa(trimmed);
      // Re-read the status before leaving, so the route gate does not send
      // the user straight back (AC5).
      await refreshMfaStatus(queryClient);
      if (result.method === "totp") {
        navigate(next, { replace: true });
      } else {
        setCode("");
        setRecoveryRemaining(result.recovery_codes_remaining);
      }
    } catch (caught) {
      if (apiErrorInfo(caught).code === "mfa_not_enrolled") {
        navigate(`/mfa/setup?next=${encodeURIComponent(next)}`, { replace: true });
        return;
      }
      const mapped = mfaFormError(caught, lang, copy.errors.generic);
      setError(mapped);
      if (mapped.kind === "code_invalid") {
        requestAnimationFrame(() => codeInput.current?.focus());
      }
    } finally {
      setBusy(false);
    }
  }

  const signOutButton = (
    <button type="button" className="auth-text-button" onClick={() => void handleSignOut()}>
      {copy.common.signOut}
    </button>
  );

  if (recoveryRemaining !== null) {
    return (
      <AuthCard title={copy.verify.title} description={copy.verify.intro} footer={signOutButton}>
        <p className="m-0 text-sm leading-relaxed">
          {copy.codes.usedRecovery(formatNumber(recoveryRemaining, lang))}
        </p>
        {recoveryRemaining <= 3 && (
          <p className="mt-3 mb-0 text-sm">
            <Link to="/account/security">{copy.codes.makeNew}</Link>
          </p>
        )}
        <button type="button" className="auth-submit mt-4" onClick={() => navigate(next, { replace: true })}>
          {copy.codes.continueLabel}
        </button>
      </AuthCard>
    );
  }

  const isRecovery = mode === "recovery";
  return (
    <AuthCard title={copy.verify.title} description={copy.verify.intro} footer={signOutButton}>
      <form className="auth-form" onSubmit={submit} noValidate>
        <div className="auth-field">
          <label htmlFor="mfa-verify-code">
            {isRecovery ? copy.verify.recoveryField : copy.verify.codeField}
          </label>
          <div className="auth-input-wrap">
            {isRecovery ? <KeyRound aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}
            <input
              id="mfa-verify-code"
              ref={codeInput}
              className="auth-input"
              dir="ltr"
              {...isRecovery
                ? { autoComplete: "off", autoCapitalize: "off", spellCheck: false }
                : { inputMode: "numeric" as const, autoComplete: "one-time-code" }}
              value={code}
              onChange={(event) => setCode(event.target.value)}
            />
          </div>
        </div>

        {error && <FormAlert>{errorText(error)}</FormAlert>}

        <SubmitButton busy={busy}>{copy.verify.submit}</SubmitButton>
      </form>

      <div className="auth-form-link-row">
        <button
          type="button"
          className="auth-text-button"
          onClick={() => switchMode(isRecovery ? "totp" : "recovery")}
        >
          {isRecovery ? copy.verify.useAuthenticator : copy.verify.useRecovery}
        </button>
      </div>
    </AuthCard>
  );
}
