import { useEffect, useState, type FormEvent } from "react";
import { KeyRound } from "lucide-react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { apiErrorInfo } from "@/lib/api-errors";
import { requestPhoneOtp, verifyPhoneOtp } from "@/lib/auth";
import { useAuth } from "@/lib/auth-context";
import { formatClock, isolateLtr, toAsciiDigits } from "@/lib/format";
import { otpRouteState, readOtpRouteState, type OtpRouteState } from "@/lib/otp-state";
import { authErrorText, useAuthCopy } from "@/components/auth/auth-copy";
import { AuthCard, AuthField, FormAlert, SubmitButton } from "@/components/auth/AuthUi";

function useSecondTicker() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return [now, setNow] as const;
}

export default function PhoneOtpVerifyPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { refreshUser } = useAuth();
  const { copy, lang } = useAuthCopy();
  const [now, setNow] = useSecondTicker();
  const [challenge, setChallenge] = useState<OtpRouteState | null>(() => readOtpRouteState(location.state));
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [resending, setResending] = useState(false);

  if (!challenge) {
    return (
      <AuthCard title={copy.otp.title} description={copy.otp.noChallenge}>
        <Link className="auth-primary-link" to="../phone">{copy.otp.requestCode}</Link>
      </AuthCard>
    );
  }

  const current = challenge;
  const secondsLeft = Math.max(0, Math.ceil((current.expiresAt - now) / 1000));
  const resendIn = Math.max(0, Math.ceil((current.resendAt - now) / 1000));
  const expired = secondsLeft === 0;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (expired) return;
    setError("");
    setNotice("");
    setLoading(true);

    try {
      await verifyPhoneOtp(current.challengeId, code);
      setCode("");
      await refreshUser();
      navigate("/", { replace: true });
    } catch (submitError) {
      setError(authErrorText(submitError, copy, lang, copy.otp.failed));
    } finally {
      setLoading(false);
    }
  }

  async function resend() {
    setError("");
    setNotice("");
    setResending(true);
    try {
      const issued = await requestPhoneOtp(current.phoneNumber);
      const at = Date.now();
      const next = otpRouteState(current.phoneNumber, issued, at);
      setNow(at);
      setChallenge(next);
      setCode("");
      setNotice(copy.otp.newCodeSent);
      // Keep the new challenge if the page is reloaded.
      navigate(".", { replace: true, state: next });
    } catch (resendError) {
      const info = apiErrorInfo(resendError);
      if (info.code === "otp_cooldown" && info.retryAfterSeconds !== null) {
        const at = Date.now();
        setNow(at);
        setChallenge({ ...current, resendAt: at + info.retryAfterSeconds * 1000 });
      }
      setError(
        info.status !== null && info.status >= 500
          ? copy.phone.failed
          : authErrorText(resendError, copy, lang, copy.otp.failed),
      );
    } finally {
      setResending(false);
    }
  }

  return (
    <AuthCard
      title={copy.otp.title}
      description={copy.otp.description(isolateLtr(current.phoneNumber))}
      footer={<p><Link to="../login">{copy.common.backToSignIn}</Link></p>}
    >
      {notice && <FormAlert tone="success">{notice}</FormAlert>}
      {error && <FormAlert>{error}</FormAlert>}
      {expired && <FormAlert>{copy.otp.expired}</FormAlert>}
      <form className="auth-form" onSubmit={handleSubmit}>
        <AuthField
          label={copy.otp.label}
          icon={KeyRound}
          dir="ltr"
          value={code}
          onChange={(event) => setCode(toAsciiDigits(event.target.value).replace(/\D/g, "").slice(0, 6))}
          autoComplete="one-time-code"
          inputMode="numeric"
          pattern="[0-9]{6}"
          minLength={6}
          maxLength={6}
          placeholder="000000"
          hint={expired ? undefined : copy.otp.expiresIn(formatClock(secondsLeft, lang))}
          disabled={expired}
          required
          autoFocus
        />
        <SubmitButton busy={loading} disabled={expired}>{copy.otp.submit}</SubmitButton>
      </form>
      <div className="auth-alternate-link">
        {resendIn > 0 ? (
          <p className="auth-field-hint">{copy.otp.resendIn(formatClock(resendIn, lang))}</p>
        ) : (
          <button type="button" className="auth-text-button" onClick={resend} disabled={resending}>
            {copy.otp.resend}
          </button>
        )}
      </div>
    </AuthCard>
  );
}
