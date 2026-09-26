import { useState, type FormEvent } from "react";
import { Mail } from "lucide-react";
import { Link } from "react-router-dom";
import { requestPasswordReset } from "@/lib/auth";
import { isolateLtr } from "@/lib/format";
import { authErrorText, useAuthCopy } from "@/components/auth/auth-copy";
import { AuthCard, AuthField, FormAlert, SubmitButton } from "@/components/auth/AuthUi";

export default function ForgotPasswordPage() {
  const { copy, lang } = useAuthCopy();
  const [email, setEmail] = useState("");
  const [error, setError] = useState("");
  const [submittedFor, setSubmittedFor] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);

    const address = email.trim();
    try {
      await requestPasswordReset(address);
      setSubmittedFor(address);
    } catch (submitError) {
      setError(authErrorText(submitError, copy, lang, copy.forgot.failed));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard
      title={copy.forgot.title}
      description={copy.forgot.description}
      footer={<p>{copy.forgot.remembered} <Link to="../login">{copy.common.backToSignIn}</Link></p>}
    >
      {submittedFor ? (
        <div className="auth-complete-state">
          <FormAlert tone="success">{copy.forgot.accepted(isolateLtr(submittedFor))}</FormAlert>
          <Link className="auth-primary-link" to="../login">{copy.common.backToSignIn}</Link>
        </div>
      ) : (
        <>
          {error && <FormAlert>{error}</FormAlert>}
          <form className="auth-form" onSubmit={handleSubmit}>
            <AuthField
              label={copy.common.email}
              icon={Mail}
              type="email"
              dir="ltr"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              inputMode="email"
              placeholder="name@company.com"
              required
              autoFocus
            />
            <SubmitButton busy={loading}>{copy.forgot.submit}</SubmitButton>
          </form>
        </>
      )}
    </AuthCard>
  );
}
