import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { MailWarning } from "lucide-react";
import { apiErrorInfo, minutesUntil } from "@/lib/api-errors";
import { resendVerificationEmail } from "@/lib/auth";
import { useAuth } from "@/lib/auth-context";
import { describeApiError } from "@/lib/error-messages";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import Sidebar from "./Sidebar";
import Header from "./Header";
import WorkspaceGate from "./WorkspaceGate";

/**
 * Uploads need a verified email or phone (403 verified_identity_required), so
 * an unverified account is told before it tries, and can ask for a new link
 * (POST /auth/verify-email/resend, gap G2): one per cooldown, the newest link
 * supersedes older ones.
 */
function VerificationNotice() {
  const { user, refreshUser } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [state, setState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");
  const [coolingUntil, setCoolingUntil] = useState<number | null>(null);

  useEffect(() => {
    if (coolingUntil === null) return;
    const timer = window.setTimeout(() => setCoolingUntil(null), Math.max(0, coolingUntil - Date.now()));
    return () => window.clearTimeout(timer);
  }, [coolingUntil]);

  if (!user || user.email_verified || user.phone_verified) return null;
  const cooling = coolingUntil !== null;

  async function resend() {
    if (state === "sending" || cooling) return;
    setState("sending");
    setMessage("");
    try {
      const result = await resendVerificationEmail();
      if (result.status === "already_verified") {
        await refreshUser().catch(() => undefined);
        return;
      }
      setState("sent");
      setCoolingUntil(Date.now() + result.resend_after * 1000);
      setMessage(
        isFa
          ? "پیوند تازهٔ تأیید به ایمیل شما فرستاده شد؛ پیوندهای قبلی دیگر کار نمی‌کنند. اگر نرسید، پوشهٔ هرزنامه را هم ببینید."
          : "A new verification link was sent to your email; earlier links no longer work. Check your spam folder if it does not arrive.",
      );
    } catch (error) {
      const info = apiErrorInfo(error);
      setState("error");
      if (info.code === "verification_cooldown") {
        const seconds = info.retryAfterSeconds ?? 60;
        setCoolingUntil(Date.now() + seconds * 1000);
        setMessage(
          isFa
            ? `پیوند به‌تازگی فرستاده شده است؛ ${formatNumber(minutesUntil(seconds), lang)} دقیقهٔ دیگر دوباره تلاش کنید.`
            : `A link was sent recently; try again in ${minutesUntil(seconds)} minute(s).`,
        );
      } else {
        setMessage(
          describeApiError(error, {
            lang,
            fallback: isFa ? "ارسال پیوند تأیید انجام نشد." : "The verification link could not be sent.",
          }).text,
        );
      }
    }
  }

  return (
    <div className="workspace-verification-notice" role="status">
      <MailWarning size={16} aria-hidden="true" />
      <span>
        {isFa
          ? "ایمیل شما هنوز تأیید نشده است. تا تأیید آن، بارگذاری فایل صوتی ممکن نیست؛ پیوند ایمیل تأیید را باز کنید."
          : "Your email is not verified yet. Uploading recordings is blocked until it is; open the link from the verification email."}
        {message && <> <strong>{message}</strong></>}
      </span>
      <button
        type="button"
        className="platform-button"
        onClick={() => void resend()}
        aria-disabled={state === "sending" || cooling}
      >
        {state === "sending"
          ? (isFa ? "در حال ارسال" : "Sending")
          : (isFa ? "ارسال دوبارهٔ ایمیل تأیید" : "Resend verification email")}
      </button>
      <Link to="/verify-email">{isFa ? "وارد کردن کد تأیید" : "Enter a verification token"}</Link>
    </div>
  );
}

export default function AppLayout() {
  const [navigationOpen, setNavigationOpen] = useState(false);
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const location = useLocation();
  useEffect(() => {
    if (!navigationOpen) return;
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setNavigationOpen(false);
    }
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [navigationOpen]);

  // Accepting an invitation must work before any workspace is selected.
  const gated = !workspace && !location.pathname.startsWith("/invitations/accept");

  return (
    <div className="workspace-shell">
      <Sidebar open={navigationOpen} onClose={() => setNavigationOpen(false)} />
      <div className="workspace-frame">
        <Header onOpenNavigation={() => setNavigationOpen(true)} />
        <main className="workspace-main">
          <VerificationNotice />
          {gated ? <WorkspaceGate /> : <Outlet />}
        </main>
      </div>
      {navigationOpen && (
        <button
          className="workspace-navigation-scrim"
          aria-label={lang === "fa" ? "بستن منو" : "Close navigation"}
          onClick={() => setNavigationOpen(false)}
        />
      )}
    </div>
  );
}
