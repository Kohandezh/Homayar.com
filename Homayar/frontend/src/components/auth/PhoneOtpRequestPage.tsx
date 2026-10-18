import { useState, type FormEvent } from "react";
import { Phone } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { apiErrorInfo } from "@/lib/api-errors";
import { requestPhoneOtp } from "@/lib/auth";
import { useAuthMethods } from "@/lib/auth-methods";
import { toAsciiDigits } from "@/lib/format";
import { authErrorText, useAuthCopy } from "@/components/auth/auth-copy";
import { AuthCard, AuthField, FormAlert, SubmitButton } from "@/components/auth/AuthUi";
import { otpRouteState } from "@/lib/otp-state";

export default function PhoneOtpRequestPage() {
  const navigate = useNavigate();
  const { copy, lang } = useAuthCopy();
  const [phoneNumber, setPhoneNumber] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { methods, status } = useAuthMethods();

  if (status === "loading") {
    return (
      <AuthCard title={copy.phone.title} description={copy.phone.description}>
        <p role="status">{copy.common.loading}</p>
      </AuthCard>
    );
  }
  if (!methods.phone) {
    return (
      <AuthCard title={copy.phone.unavailableTitle} description={copy.phone.unavailableBody}>
        <Link className="auth-primary-link" to="../login">{copy.common.backToSignIn}</Link>
      </AuthCard>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);

    try {
      const normalizedPhone = toAsciiDigits(phoneNumber).trim();
      const challenge = await requestPhoneOtp(normalizedPhone);
      navigate("verify", { state: otpRouteState(normalizedPhone, challenge) });
    } catch (submitError) {
      const info = apiErrorInfo(submitError);
      // A disabled or unconfigured SMS provider answers 503 phone_auth_unavailable
      // and a provider that refused the message 503 otp_delivery_failed (gaps
      // G1/G7); neither is ever shown as a wrong number.
      setError(
        info.code === "otp_delivery_failed"
          ? copy.phone.deliveryFailed
          : info.status !== null && info.status >= 500
            ? copy.phone.failed
            : authErrorText(submitError, copy, lang, copy.phone.failed),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard
      title={copy.phone.title}
      description={copy.phone.description}
      footer={<p>{copy.phone.useEmail} <Link to="../login">{copy.common.backToSignIn}</Link></p>}
    >
      {error && <FormAlert>{error}</FormAlert>}
      <form className="auth-form" onSubmit={handleSubmit}>
        <AuthField
          label={copy.phone.label}
          icon={Phone}
          type="tel"
          dir="ltr"
          value={phoneNumber}
          onChange={(event) => setPhoneNumber(event.target.value)}
          autoComplete="tel"
          inputMode="tel"
          placeholder="0912 123 4567"
          hint={copy.phone.hint}
          maxLength={30}
          required
          autoFocus
        />
        <SubmitButton busy={loading}>{copy.phone.submit}</SubmitButton>
      </form>
    </AuthCard>
  );
}
