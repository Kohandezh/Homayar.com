import { useState, type FormEvent } from "react";
import { Mail, UserRound } from "lucide-react";
import { Link } from "react-router";
import { register } from "@/lib/auth";
import { isolateLtr } from "@/lib/format";
import { authErrorText, useAuthCopy } from "@/components/auth/auth-copy";
import {
  AuthCard,
  AuthField,
  FormAlert,
  GoogleSignInOption,
  PasswordField,
  SubmitButton,
} from "@/components/auth/AuthUi";

export default function RegisterPage() {
  const { copy, lang } = useAuthCopy();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [createdFor, setCreatedFor] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");

    if (password !== confirmation) {
      setError(copy.common.passwordsMismatch);
      return;
    }

    setLoading(true);
    const address = email.trim();
    try {
      await register({ name: name.trim(), email: address, password, locale: lang });
      // Nothing secret stays in memory once the account exists.
      setPassword("");
      setConfirmation("");
      setCreatedFor(address);
    } catch (submitError) {
      setError(authErrorText(submitError, copy, lang, copy.register.failed));
    } finally {
      setLoading(false);
    }
  }

  return (
    <AuthCard
      title={copy.register.title}
      description={copy.register.description}
      footer={<p>{copy.register.haveAccount} <Link to="../login">{copy.register.signIn}</Link></p>}
    >
      {createdFor ? (
        <div className="auth-complete-state">
          <FormAlert tone="success">
            {copy.register.created(isolateLtr(createdFor))}
          </FormAlert>
          <p>{copy.register.createdNext}</p>
          <Link className="auth-primary-link" to="../login">{copy.common.continueToSignIn}</Link>
          <a href="/contact">{copy.common.contactSupport}</a>
        </div>
      ) : (
        <>
          <GoogleSignInOption divider={copy.common.orEmail} />
          {error && <FormAlert>{error}</FormAlert>}
          <form className="auth-form" onSubmit={handleSubmit}>
            <AuthField
              label={copy.register.fullName}
              icon={UserRound}
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoComplete="name"
              placeholder={copy.register.fullNamePlaceholder}
              maxLength={160}
              required
              autoFocus
            />
            <AuthField
              label={copy.common.workEmail}
              icon={Mail}
              type="email"
              dir="ltr"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
              inputMode="email"
              placeholder="name@company.com"
              required
            />
            <PasswordField
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="new-password"
              minLength={10}
              maxLength={128}
              hint={copy.common.passwordHint}
              required
            />
            <PasswordField
              label={copy.register.confirm}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoComplete="new-password"
              minLength={10}
              maxLength={128}
              required
            />
            <SubmitButton busy={loading}>{copy.register.submit}</SubmitButton>
          </form>
        </>
      )}
    </AuthCard>
  );
}
