import { useState, type FormEvent } from "react";
import { KeyRound } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { resetPassword } from "@/lib/auth";
import { authErrorText, useAuthCopy } from "@/components/auth/auth-copy";
import { AuthCard, AuthField, FormAlert, PasswordField, SubmitButton } from "@/components/auth/AuthUi";

export default function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const { copy, lang } = useAuthCopy();
  const linkToken = searchParams.get("token")?.trim() ?? "";
  const [token, setToken] = useState(linkToken);
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [complete, setComplete] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (password !== confirmation) {
      setError(copy.common.passwordsMismatch);
      return;
    }

    setLoading(true);
    try {
      await resetPassword(token.trim(), password);
      setPassword("");
      setConfirmation("");
      setComplete(true);
    } catch (submitError) {
      setError(authErrorText(submitError, copy, lang, copy.reset.failed));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard
      title={copy.reset.title}
      description={copy.reset.description}
      footer={<p><Link to="../login">{copy.common.backToSignIn}</Link></p>}
    >
      {complete ? (
        <div className="auth-complete-state">
          <FormAlert tone="success">{copy.reset.complete}</FormAlert>
          <Link className="auth-primary-link" to="../login">{copy.reset.signInNew}</Link>
        </div>
      ) : (
        <>
          {error && <FormAlert>{error}</FormAlert>}
          {error === copy.reset.invalid && (
            <p className="auth-alternate-link"><Link to="../forgot-password">{copy.forgot.submit}</Link></p>
          )}
          <form className="auth-form" onSubmit={handleSubmit}>
            {!linkToken && (
              <AuthField
                label={copy.reset.token}
                icon={KeyRound}
                dir="ltr"
                value={token}
                onChange={(event) => setToken(event.target.value)}
                autoComplete="off"
                spellCheck={false}
                placeholder={copy.reset.tokenPlaceholder}
                minLength={20}
                maxLength={300}
                required
                autoFocus
              />
            )}
            <PasswordField
              label={copy.reset.newPassword}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={10}
              maxLength={128}
              hint={copy.common.passwordHint}
              required
              autoFocus={Boolean(linkToken)}
            />
            <PasswordField
              label={copy.reset.confirm}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="new-password"
              minLength={10}
              maxLength={128}
              required
            />
            <SubmitButton busy={loading}>{copy.reset.submit}</SubmitButton>
          </form>
        </>
      )}
    </AuthCard>
  );
}
