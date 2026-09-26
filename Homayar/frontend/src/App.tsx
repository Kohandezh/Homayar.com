import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate, Outlet, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nProvider } from "@/lib/i18n";
import AuthProvider from "@/lib/AuthProvider";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import AppLayout from "@/components/layout/AppLayout";
import AuthLayout from "@/components/auth/AuthLayout";

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
const MembersPage = lazy(() => import("@/components/platform/MembersPage"));
const UsagePage = lazy(() => import("@/components/platform/UsagePage"));
const SettingsPage = lazy(() => import("@/components/platform/SettingsPage"));
const InvitationAcceptPage = lazy(() => import("@/components/platform/InvitationAcceptPage"));
const SharedMeetingPage = lazy(() => import("@/components/platform/SharedMeetingPage"));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 10000 } },
});

const routerBasename = import.meta.env.BASE_URL.replace(/\/$/, "") || "/";

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

function RouteFallback() {
  const { lang } = useI18n();
  return (
    <div className="session-loading" role="status" aria-live="polite">
      <span className="session-loading-mark" aria-hidden="true">H</span>
      <span>{lang === "fa" ? "در حال بارگذاری" : "Loading"}</span>
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <BrowserRouter basename={routerBasename}>
          <AuthProvider>
            <Suspense fallback={<RouteFallback />}>
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

                <Route element={<RequireAuth />}>
                  <Route element={<AppLayout />}>
                    <Route index element={<OverviewPage />} />
                    <Route path="meetings" element={<MeetingsPage />} />
                    <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
                    <Route path="search" element={<SearchPage />} />
                    <Route path="members" element={<MembersPage />} />
                    <Route path="usage" element={<UsagePage />} />
                    <Route path="settings" element={<SettingsPage />} />
                    <Route path="invitations/accept" element={<InvitationAcceptPage />} />
                  </Route>
                </Route>

                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </AuthProvider>
        </BrowserRouter>
      </I18nProvider>
    </QueryClientProvider>
  );
}
