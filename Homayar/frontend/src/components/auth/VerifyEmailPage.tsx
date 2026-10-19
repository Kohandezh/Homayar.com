import { useState, type FormEvent } from "react";
import { KeyRound } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { verifyEmail } from "@/lib/auth";
import { useAuth } from "@/lib/auth-context";
import { authErrorText, useAuthCopy } from "@/components/auth/auth-copy";
import { AuthCard, AuthField, FormAlert, SubmitButton } from "@/components/auth/AuthUi";

export default function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const { status, refreshUser } = useAuth();
  const { copy, lang } = useAuthCopy();
  const [token, setToken] = useState(() => searchParams.get("token")?.trim() ?? "");
  const [error, setError] = useState("");
  const [verified, setVerified] = useState(false);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setLoading(true);

    try {
      await verifyEmail(token.trim());
      setVerified(true);
      // A signed-in user's session now reports the verified address.
      if (status === "authenticated") await refreshUser().catch(() => undefined);
    } catch (submitError) {
      setError(authErrorText(submitError, copy, lang, copy.verify.failed));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard
      title={copy.verify.title}
      description={copy.verify.description}
      footer={<p><Link to="../login">{copy.common.backToSignIn}</Link></p>}
    >
      {verified ? (
        <div className="auth-complete-state">
          <FormAlert tone="success">{copy.verify.verified}</FormAlert>
          {status === "authenticated" ? (
            <Link className="auth-primary-link" to="/">{copy.verify.continueWorkspace}</Link>
          ) : (
            <Link className="auth-primary-link" to="../login">{copy.common.continueToSignIn}</Link>
          )}
        </div>
      ) : (
        <>
          {error && <FormAlert>{error}</FormAlert>}
          <form className="auth-form" onSubmit={handleSubmit}>
            <AuthField
              label={copy.verify.token}
              icon={KeyRound}
              dir="ltr"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              placeholder={copy.verify.tokenPlaceholder}
              minLength={20}
              maxLength={300}
              required
              autoFocus={!token}
            />
            <SubmitButton busy={loading}>{copy.verify.submit}</SubmitButton>
          </form>
        </>
      )}
    </AuthCard>
  );
}
