import { lazy, Suspense } from "react";
import { Routes, Route, Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { ADMIN_NAV } from "@/lib/navigation";
import AppLayout from "@/components/layout/AppLayout";
import AdminGuard from "@/components/layout/AdminGuard";
import AuthLayout from "@/components/auth/AuthLayout";
import ComingSoonPage from "@/components/admin/ComingSoonPage";
import { ADMIN_PAGES } from "@/components/admin/admin-pages";
import MfaGate from "@/components/mfa/MfaGate";
import MfaRequiredListener from "@/components/mfa/MfaRequiredListener";
import OrgGuard from "@/components/org/OrgGuard";

const LoginPage = lazy(() => import("@/components/auth/LoginPage"));
const RegisterPage = lazy(() => import("@/components/auth/RegisterPage"));
const VerifyEmailPage = lazy(() => import("@/components/auth/VerifyEmailPage"));
const ForgotPasswordPage = lazy(() => import("@/components/auth/ForgotPasswordPage"));
const ResetPasswordPage = lazy(() => import("@/components/auth/ResetPasswordPage"));
const PhoneOtpRequestPage = lazy(() => import("@/components/auth/PhoneOtpRequestPage"));
const PhoneOtpVerifyPage = lazy(() => import("@/components/auth/PhoneOtpVerifyPage"));

const OverviewPage = lazy(() => import("@/components/platform/OverviewPage"));
const MeetingsPage = lazy(() => import("@/components/platform/MeetingsPage"));
const MeetingDetailPage = lazy(() => import("@/components/platform/MeetingDetailPage"));
const SearchPage = lazy(() => import("@/components/platform/SearchPage"));
const TrendsPage = lazy(() => import("@/components/trends/TrendsPage"));
const VoicemailsPage = lazy(() => import("@/components/voicemail/VoicemailsPage"));
const InvitationAcceptPage = lazy(() => import("@/components/platform/InvitationAcceptPage"));
const SharedMeetingPage = lazy(() => import("@/components/platform/SharedMeetingPage"));
const ReviewQueuePage = lazy(() => import("@/components/reviews/ReviewQueuePage"));
const ReviewItemPage = lazy(() => import("@/components/reviews/ReviewItemPage"));

const OrgOverviewPage = lazy(() => import("@/components/org/OrgOverviewPage"));
const OrgMembersPage = lazy(() => import("@/components/org/OrgMembersPage"));
const OrgWorkspacesPage = lazy(() => import("@/components/org/OrgWorkspacesPage"));
const OrgScopeGrantsPage = lazy(() => import("@/components/org/OrgScopeGrantsPage"));
const OrgAuditPage = lazy(() => import("@/components/org/OrgAuditPage"));
const OrgRecordsPage = lazy(() => import("@/components/org/OrgRecordsPage"));

const MfaSetupPage = lazy(() => import("@/components/mfa/MfaSetupPage"));
const MfaVerifyPage = lazy(() => import("@/components/mfa/MfaVerifyPage"));
const AccountSecurityPage = lazy(() => import("@/components/account/AccountSecurityPage"));

function RequireAuth() {
  const { status, retrySession } = useAuth();
  const { lang } = useI18n();
  const location = useLocation();

  if (status === "loading") {
    return (
      <div className="session-loading" role="status">
        <span className="session-loading-mark" aria-hidden="true">H</span>
        <span>{lang === "fa" ? "در حال بررسی نشست" : "Checking session"}</span>
      </div>
    );
  }

  if (status === "unavailable") {
    // The API did not answer: the session may still be valid, so this is
    // neither a sign-in form nor a "session ended" message.
    return (
      <div className="session-loading" role="alert">
        <span className="session-loading-mark" aria-hidden="true">H</span>
        <span>
          {lang === "fa"
            ? "سرویس هُمیار الان پاسخ نمی‌دهد؛ نشست شما حفظ شده است."
            : "Homayar is not responding right now; your session is kept."}
        </span>
        <button type="button" className="platform-button" onClick={retrySession}>
          {lang === "fa" ? "تلاش دوباره" : "Try again"}
        </button>
      </div>
    );
  }

  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }

  return <Outlet />;
}

/** An old admin path: same query and fragment, new place. */
function MovedTo({ pathname }: { pathname: string }) {
  const { search, hash } = useLocation();
  return <Navigate to={{ pathname, search, hash }} replace />;
}

/**
 * A Suspense boundary inside the MFA gate, so a lazy page loading keeps the
 * gate (and its MFA status query observer) mounted: with only App.tsx's
 * outer boundary, every first page load would unmount the gate and, when the
 * status read had errored (an old backend), remount into an endless refetch
 * loop.
 */
function PageSuspense() {
  const { lang } = useI18n();
  return (
    <Suspense
      fallback={
        <div className="session-loading" role="status">
          <span className="session-loading-mark" aria-hidden="true">H</span>
          <span>{lang === "fa" ? "در حال بارگذاری صفحه" : "Loading page"}</span>
        </div>
      }
    >
      <Outlet />
    </Suspense>
  );
}

function AdminPage({ entry }: { entry: (typeof ADMIN_NAV)[number] }) {
  const Page = ADMIN_PAGES[entry.path];
  return Page ? <Page /> : <ComingSoonPage entry={entry} />;
}

/** Every console route. App.tsx supplies the router and the providers. */
export function AppRoutes() {
  return (
    <>
      {/* A 403 mfa_* from any request moves the user to the matching MFA page (AC3). */}
      <MfaRequiredListener />
      <Routes>
        <Route element={<AuthLayout />}>
          <Route path="login" element={<LoginPage />} />
          <Route path="register" element={<RegisterPage />} />
          <Route path="verify-email" element={<VerifyEmailPage />} />
          <Route path="forgot-password" element={<ForgotPasswordPage />} />
          <Route path="reset-password" element={<ResetPasswordPage />} />
          <Route path="phone" element={<PhoneOtpRequestPage />} />
          <Route path="phone/verify" element={<PhoneOtpVerifyPage />} />
        </Route>

        <Route path="share/:shareToken" element={<SharedMeetingPage />} />

        {/* The admin pages moved under /admin; the target still passes auth, the gate and the guard. */}
        <Route path="members" element={<MovedTo pathname="/admin/members" />} />
        <Route path="usage" element={<MovedTo pathname="/admin/usage" />} />
        <Route path="settings" element={<MovedTo pathname="/admin/settings" />} />

        <Route element={<RequireAuth />}>
          {/* The MFA pages need a session but render outside AppLayout and
              WorkspaceGate (the auth-page look), so they never fire a
              workspace request the MFA gate would refuse. */}
          <Route element={<AuthLayout />}>
            <Route path="mfa/setup" element={<MfaSetupPage />} />
            <Route path="mfa/verify" element={<MfaVerifyPage />} />
          </Route>

          {/* The proactive MFA check (AC2) runs before the app shell renders.
              The Suspense boundary inside it keeps the gate mounted while a
              lazy page loads. */}
          <Route element={<MfaGate />}>
            <Route element={<PageSuspense />}>
              <Route element={<AppLayout />}>
                <Route index element={<OverviewPage />} />
                <Route path="account/security" element={<AccountSecurityPage />} />
                <Route path="meetings" element={<MeetingsPage />} />
             <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
             {/* The review queue (HOM-265): a main page, not an admin page, but it
                 still needs review:read, so it goes through the same guard. */}
             <Route
               path="reviews"
               element={
                 <AdminGuard entry={{ permission: "review:read" }}>
                   <ReviewQueuePage />
                 </AdminGuard>
               }
             />
             <Route
               path="reviews/:itemId"
               element={
                 <AdminGuard entry={{ permission: "review:read" }}>
                   <ReviewItemPage />
                 </AdminGuard>
               }
             />
             <Route path="trends" element={<TrendsPage />} />
             <Route path="voicemails" element={<VoicemailsPage />} />
             <Route path="search" element={<SearchPage />} />
             <Route path="invitations/accept" element={<InvitationAcceptPage />} />
             {ADMIN_NAV.map((entry) => (
               <Route
                 key={entry.path}
                 path={entry.path}
                 element={
                   <AdminGuard entry={entry}>
                     <AdminPage entry={entry} />
                   </AdminGuard>
                 }
               />
             ))}
             {/* Organization pages: guarded by the org role from GET /organizations/{id}, not by the workspace. */}
             <Route path="org" element={<Navigate to="/" replace />} />
             <Route
               path="org/:organizationId"
               element={<OrgGuard permission="organization:analytics:read"><OrgOverviewPage /></OrgGuard>}
             />
             <Route
               path="org/:organizationId/members"
               element={<OrgGuard permission="organization:member:read"><OrgMembersPage /></OrgGuard>}
             />
             <Route
               path="org/:organizationId/workspaces"
               element={<OrgGuard permission="organization:workspace:read"><OrgWorkspacesPage /></OrgGuard>}
             />
             <Route
               path="org/:organizationId/scope-grants"
               element={<OrgGuard permission="organization:member:read"><OrgScopeGrantsPage /></OrgGuard>}
             />
             <Route
               path="org/:organizationId/audit"
               element={<OrgGuard permission="organization:audit:read"><OrgAuditPage /></OrgGuard>}
             />
          <Route
            path="org/:organizationId/records"
            element={<OrgGuard permission="organization:records:read"><OrgRecordsPage /></OrgGuard>}
          />
              </Route>
            </Route>
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </>
  );
}
