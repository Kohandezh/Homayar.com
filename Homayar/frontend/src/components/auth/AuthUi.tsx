import {
  AlertCircle,
  CheckCircle2,
  Eye,
  EyeOff,
  Info,
  LoaderCircle,
  LockKeyhole,
  type LucideIcon,
} from "lucide-react";
import {
  useId,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
} from "react";
import { startGoogleSignIn } from "@/lib/auth";
import { useAuthMethods } from "@/lib/auth-methods";
import { useAuthCopy } from "@/components/auth/auth-copy";

interface AuthCardProps {
  title: string;
  description: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function AuthCard({ title, description, children, footer }: AuthCardProps) {
  return (
    <section className="auth-card" aria-labelledby="auth-page-title">
      <header className="auth-card-header">
        <h1 id="auth-page-title">{title}</h1>
        <p>{description}</p>
      </header>
      {children}
      {footer && <footer className="auth-card-footer">{footer}</footer>}
    </section>
  );
}

interface AuthFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "className"> {
  label: string;
  icon: LucideIcon;
  hint?: string;
}

export function AuthField({ label, icon: Icon, hint, id, ...inputProps }: AuthFieldProps) {
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;

  return (
    <div className="auth-field">
      <label htmlFor={inputId}>{label}</label>
      <div className="auth-input-wrap">
        <Icon aria-hidden="true" />
        <input id={inputId} className="auth-input" aria-describedby={hintId} {...inputProps} />
      </div>
      {hint && <p id={hintId} className="auth-field-hint">{hint}</p>}
    </div>
  );
}

interface PasswordFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "className" | "type"> {
  label?: string;
  hint?: string;
}

export function PasswordField({ label, hint, id, ...inputProps }: PasswordFieldProps) {
  const { copy } = useAuthCopy();
  const generatedId = useId();
  const inputId = id ?? generatedId;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const [visible, setVisible] = useState(false);

  return (
    <div className="auth-field">
      <label htmlFor={inputId}>{label ?? copy.common.password}</label>
      <div className="auth-input-wrap auth-password-wrap">
        <LockKeyhole aria-hidden="true" />
        <input
          id={inputId}
          type={visible ? "text" : "password"}
          className="auth-input"
          aria-describedby={hintId}
          {...inputProps}
        />
        <button
          type="button"
          className="auth-password-toggle"
          onClick={() => setVisible((current) => !current)}
          aria-label={visible ? copy.common.hidePassword : copy.common.showPassword}
          aria-pressed={visible}
        >
          {visible ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
        </button>
      </div>
      {hint && <p id={hintId} className="auth-field-hint">{hint}</p>}
    </div>
  );
}

export function FormAlert({ children, tone = "error" }: { children: ReactNode; tone?: "error" | "success" | "info" }) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "info" ? Info : AlertCircle;
  return (
    <div className={`auth-alert auth-alert-${tone}`} role={tone === "error" ? "alert" : "status"}>
      <Icon aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

interface SubmitButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  busy?: boolean;
  children: ReactNode;
}

export function SubmitButton({ busy = false, children, disabled, ...props }: SubmitButtonProps) {
  return (
    <button className="auth-submit" type="submit" disabled={disabled || busy} {...props}>
      {busy && <LoaderCircle className="auth-spinner" aria-hidden="true" />}
      <span>{children}</span>
    </button>
  );
}

/** Google sign-in and its divider, only when the server reports Google available (gap G1). */
export function GoogleSignInOption({ divider }: { divider: ReactNode }) {
  const { copy } = useAuthCopy();
  const { methods } = useAuthMethods();
  if (!methods.google) return null;
  return (
    <>
      <button type="button" className="auth-google" onClick={startGoogleSignIn}>
        <span className="auth-google-mark" aria-hidden="true">G</span>
        <span>{copy.common.google}</span>
      </button>
      <AuthDivider>{divider}</AuthDivider>
    </>
  );
}

export function AuthDivider({ children }: { children: ReactNode }) {
  return <div className="auth-divider"><span>{children}</span></div>;
}
