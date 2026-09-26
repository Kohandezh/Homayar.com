import { useState, type FormEvent } from "react";
import { Mail } from "lucide-react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "@/lib/auth-context";
import { useAuthMethods } from "@/lib/auth-methods";
import { safeReturnPath } from "@/lib/return-path";
import { authErrorText, useAuthCopy, type AuthCopy } from "@/components/auth/auth-copy";
import {
  AuthCard,
  AuthField,
  FormAlert,
  GoogleSignInOption,
  PasswordField,
  SubmitButton,
} from "@/components/auth/AuthUi";

function googleError(search: string, copy: AuthCopy): string {
  const code = new URLSearchParams(search).get("error");
  if (code === "account_link_required") return copy.login.googleLinkRequired;
  if (code) return copy.login.googleFailed;
  return "";
}

export default function LoginPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { signIn, status, endReason } = useAuth();
  const { copy, lang } = useAuthCopy();
  const { methods } = useAuthMethods();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  if (status === "authenticated") return <Navigate to={safeReturnPath(location.state)} replace />;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setLoading(true);

    try {
      await signIn({ email: email.trim(), password });
      navigate(safeReturnPath(location.state), { replace: true });
    } catch (submitError) {
      setError(submitError);
    } finally {
      setLoading(false);
    }
  }

  const callbackError = googleError(location.search, copy);
  const message = error ? authErrorText(error, copy, lang, copy.login.failed) : callbackError;

  return (
    <AuthCard
      title={copy.login.title}
      description={copy.login.description}
      footer={<p>{copy.login.newHere} <Link to="../register">{copy.login.createAccount}</Link></p>}
    >
      {endReason === "expired" && !message && <FormAlert tone="info">{copy.login.sessionExpired}</FormAlert>}
      <GoogleSignInOption divider={copy.common.orEmail} />

      {message && <FormAlert>{message}</FormAlert>}

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
        <PasswordField
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          autoComplete="current-password"
          required
        />
        <div className="auth-form-link-row">
          <Link to="../forgot-password">{copy.login.forgot}</Link>
        </div>
        <SubmitButton busy={loading}>{copy.login.submit}</SubmitButton>
      </form>

      {methods.phone && (
        <div className="auth-alternate-link">
          <Link to="../phone">{copy.login.phone}</Link>
        </div>
      )}
    </AuthCard>
  );
}
