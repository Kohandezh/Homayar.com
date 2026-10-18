import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { MailWarning } from "lucide-react";
import { Alert, Button, Drawer, linkVariants } from "@heroui/react";
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
    <Alert status="warning" role="status" className="mb-4">
      <Alert.Indicator>
        <MailWarning size={16} aria-hidden="true" />
      </Alert.Indicator>
      <Alert.Content className="gap-2">
        <Alert.Description>
          {isFa
            ? "ایمیل شما هنوز تأیید نشده است. تا تأیید آن، بارگذاری فایل صوتی ممکن نیست؛ پیوند ایمیل تأیید را باز کنید."
            : "Your email is not verified yet. Uploading recordings is blocked until it is; open the link from the verification email."}
          {message && <> <strong>{message}</strong></>}
        </Alert.Description>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            size="sm"
            variant="secondary"
            isPending={state === "sending"}
            // Still focusable during the cooldown, so the reason stays readable next to it.
            aria-disabled={cooling || undefined}
            onPress={() => void resend()}
          >
            {state === "sending"
              ? (isFa ? "در حال ارسال" : "Sending")
              : (isFa ? "ارسال دوبارهٔ ایمیل تأیید" : "Resend verification email")}
          </Button>
          <Link to="/verify-email" className={linkVariants().base()}>
            {isFa ? "وارد کردن کد تأیید" : "Enter a verification token"}
          </Link>
        </div>
      </Alert.Content>
    </Alert>
  );
}

/** Below this width the sidebar lives in the drawer (Tailwind `lg`). */
const DESKTOP_QUERY = "(min-width: 1024px)";

export default function AppLayout() {
  const [navigationOpen, setNavigationOpen] = useState(false);
  const { workspace } = useAuth();
  const { lang, dir } = useI18n();
  const location = useLocation();
  const isFa = lang === "fa";

  // A drawer left open while the window grows would keep the page locked
  // behind an invisible modal.
  useEffect(() => {
    if (!navigationOpen || typeof window.matchMedia !== "function") return;
    const desktop = window.matchMedia(DESKTOP_QUERY);
    function closeOnDesktop() {
      if (desktop.matches) setNavigationOpen(false);
    }
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, [navigationOpen]);

  // Accepting an invitation must work before any workspace is selected.
  const gated = !workspace && !location.pathname.startsWith("/invitations/accept");

  return (
    <div className="flex min-h-dvh bg-background text-foreground">
      {/* First in the row, so it sits on the right in Persian and on the left in English. */}
      <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-e border-separator bg-surface p-4 lg:flex">
        <Sidebar />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <Header onOpenNavigation={() => setNavigationOpen(true)} />
        <main className="min-w-0 flex-1 p-4 sm:p-6">
          <VerificationNotice />
          {gated ? <WorkspaceGate /> : <Outlet />}
        </main>
      </div>

      <Drawer.Backdrop isOpen={navigationOpen} onOpenChange={setNavigationOpen}>
        {/* HeroUI slides and drags by the physical `placement`, but positions the panel with
            flex justify-start/end, which follow the text direction. The physical side keeps the
            motion right; `justify-start` keeps the panel on the reading-start edge in both. */}
        <Drawer.Content placement={dir === "rtl" ? "right" : "left"} className="justify-start">
          <Drawer.Dialog aria-label={isFa ? "منو" : "Navigation"} className="w-72 max-w-[85vw] p-4">
            <Drawer.CloseTrigger aria-label={isFa ? "بستن منو" : "Close navigation"} />
            <Drawer.Body className="flex min-h-0 flex-1 flex-col">
              <Sidebar onNavigate={() => setNavigationOpen(false)} />
            </Drawer.Body>
          </Drawer.Dialog>
        </Drawer.Content>
      </Drawer.Backdrop>
    </div>
  );
}
