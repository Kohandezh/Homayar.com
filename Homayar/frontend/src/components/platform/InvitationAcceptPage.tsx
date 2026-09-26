import { useState, type FormEvent } from "react";
import { ArrowRight, CheckCircle2, LogIn, MailCheck } from "lucide-react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";

import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { useAcceptInvitation } from "@/lib/platform-api";
import type { InvitationAcceptedResponse } from "@/types/platform";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";

/** Refusals of POST /members/invitations/accept (backend/services/member_service.py). */
const ACCEPT_ERROR_CODES: ErrorCodeMessages = {
  invitation_not_found: { fa: "این دعوت پیدا نشد.", en: "This invitation was not found." },
  invitation_expired: { fa: "این دعوت منقضی شده است؛ از مدیر فضای کاری دعوت تازه بخواهید.", en: "This invitation has expired; ask the workspace administrator for a new one." },
  invitation_revoked: { fa: "این دعوت لغو شده است.", en: "This invitation has been revoked." },
  invitation_already_accepted: { fa: "این دعوت پیش‌تر پذیرفته شده است.", en: "This invitation was already accepted." },
  invitation_identity_mismatch: { fa: "این دعوت برای حساب دیگری فرستاده شده است؛ با همان ایمیل وارد شوید.", en: "This invitation was sent to another account; sign in with that email." },
};

export default function InvitationAcceptPage() {
  const { status, refreshUser } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const token = searchParams.get("token")?.trim() ?? "";
  const tokenIsValid = token.length >= 32 && token.length <= 256;
  const acceptMutation = useAcceptInvitation();
  const [accepted, setAccepted] = useState<InvitationAcceptedResponse | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState<unknown>(null);

  async function accept(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!tokenIsValid || status !== "authenticated" || acceptMutation.isPending) return;
    setRefreshError(null);
    try {
      const result = await acceptMutation.mutateAsync(token);
      setAccepted(result);
      setSearchParams({}, { replace: true });
      setIsRefreshing(true);
      try {
        await refreshUser();
      } catch (error) {
        setRefreshError(error);
      } finally {
        setIsRefreshing(false);
      }
    } catch {
      // The mutation exposes its sanitized API error below.
    }
  }

  if (status === "loading") {
    return <LoadingState label={isFa ? "در حال بررسی نشست" : "Checking your session"} />;
  }

  return (
    <main className="platform-page platform-invitation-accept-page max-w-2xl">
      <PageHeader
        eyebrow={isFa ? "دسترسی فضای کاری" : "Workspace access"}
        title={isFa ? "پذیرش دعوت‌نامه" : "Accept invitation"}
      />

      <section className="platform-invitation-accept platform-surface p-5 sm:p-7" aria-live="polite">
        {accepted ? (
          <div className="grid justify-items-start gap-5">
            <span className="grid size-11 place-items-center rounded-full bg-success/10 text-success" aria-hidden="true">
              <CheckCircle2 size={24} />
            </span>
            <div>
              <h2 className="m-0 text-lg font-semibold text-text-primary">
                {isFa ? "دعوت‌نامه پذیرفته شد" : "Invitation accepted"}
              </h2>
              <p className="mt-2 mb-0 text-sm leading-6 text-text-secondary">
                {isFa
                  ? `شما اکنون عضو فضای کاری «${accepted.workspace_name}» هستید.`
                  : `You are now a member of ${accepted.workspace_name}.`}
              </p>
              <p className="mt-1 mb-0 text-xs text-text-muted">
                {isFa ? "نقش شما:" : "Your role:"} {accepted.membership.role}
              </p>
            </div>

            {isRefreshing && (
              <LoadingState label={isFa ? "در حال تازه‌سازی فضاهای کاری" : "Refreshing workspaces"} />
            )}
            {refreshError !== null && (
              <ErrorState
                error={refreshError}
                fallback={isFa ? "دعوت پذیرفته شد، اما فهرست فضاها تازه نشد" : "The invitation was accepted, but workspaces could not refresh"}
              />
            )}

            <button
              type="button"
              className="platform-button platform-button-primary"
              onClick={() => navigate("/", { replace: true })}
              disabled={isRefreshing}
            >
              {isFa ? "ورود به همیار" : "Continue to Homayar"}
              <ArrowRight aria-hidden="true" size={15} />
            </button>
          </div>
        ) : status !== "authenticated" ? (
          <div className="grid justify-items-start gap-4">
            <LogIn aria-hidden="true" className="text-text-muted" size={24} />
            <div>
              <h2 className="m-0 text-base font-semibold text-text-primary">
                {isFa ? "ابتدا وارد شوید" : "Sign in first"}
              </h2>
              <p className="mt-2 mb-0 text-sm text-text-secondary">
                {isFa ? "پذیرش دعوت فقط با حساب احراز هویت‌شده انجام می‌شود." : "Invitations can only be accepted from an authenticated account."}
              </p>
            </div>
            <button
              type="button"
              className="platform-button platform-button-primary"
              onClick={() => navigate("/login", { state: { from: location } })}
            >
              <LogIn aria-hidden="true" size={15} />
              {isFa ? "ورود" : "Sign in"}
            </button>
          </div>
        ) : !tokenIsValid ? (
          <ErrorState
            error={new Error(isFa ? "لینک دعوت ناقص یا نامعتبر است" : "This invitation link is incomplete or invalid")}
            fallback={isFa ? "لینک دعوت معتبر نیست" : "Invalid invitation link"}
          />
        ) : (
          <form className="grid justify-items-start gap-5" onSubmit={accept}>
            <span className="grid size-11 place-items-center rounded-full bg-bg-elevated text-accent" aria-hidden="true">
              <MailCheck size={23} />
            </span>
            <div>
              <h2 className="m-0 text-base font-semibold text-text-primary">
                {isFa ? "پیوستن به فضای کاری" : "Join the workspace"}
              </h2>
              <p className="mt-2 mb-0 text-sm leading-6 text-text-secondary">
                {isFa
                  ? "با تأیید، حساب فعلی شما به فضای کاری دعوت‌کننده اضافه می‌شود. این لینک شخصی است."
                  : "Confirm to add your current account to the inviting workspace. This link is personal."}
              </p>
            </div>

            {acceptMutation.error && (
              <ErrorState
                error={acceptMutation.error}
                codes={ACCEPT_ERROR_CODES}
                fallback={isFa ? "پذیرش دعوت‌نامه ناموفق بود." : "Could not accept the invitation."}
              />
            )}

            <button type="submit" className="platform-button platform-button-primary" disabled={acceptMutation.isPending}>
              <MailCheck aria-hidden="true" size={15} />
              {acceptMutation.isPending ? (isFa ? "در حال تأیید" : "Accepting") : (isFa ? "پذیرش دعوت" : "Accept invitation")}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
