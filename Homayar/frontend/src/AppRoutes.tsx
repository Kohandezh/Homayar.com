import { lazy } from "react";
import { Routes, Route, Navigate, Outlet, useLocation } from "react-router";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { ADMIN_NAV } from "@/lib/navigation";
import AppLayout from "@/components/layout/AppLayout";
import AdminGuard from "@/components/layout/AdminGuard";
import AuthLayout from "@/components/auth/AuthLayout";
import ComingSoonPage from "@/components/admin/ComingSoonPage";
import { ADMIN_PAGES } from "@/components/admin/admin-pages";
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

const OrgOverviewPage = lazy(() => import("@/components/org/OrgOverviewPage"));
const OrgMembersPage = lazy(() => import("@/components/org/OrgMembersPage"));
const OrgWorkspacesPage = lazy(() => import("@/components/org/OrgWorkspacesPage"));
const OrgScopeGrantsPage = lazy(() => import("@/components/org/OrgScopeGrantsPage"));

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

function AdminPage({ entry }: { entry: (typeof ADMIN_NAV)[number] }) {
  const Page = ADMIN_PAGES[entry.path];
  return Page ? <Page /> : <ComingSoonPage entry={entry} />;
}

/** Every console route. App.tsx supplies the router and the providers. */
export function AppRoutes() {
  return (
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
        <Route element={<AppLayout />}>
          <Route index element={<OverviewPage />} />
          <Route path="meetings" element={<MeetingsPage />} />
          <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
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
        </Route>
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
