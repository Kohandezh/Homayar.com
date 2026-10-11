import { isAxiosError } from "axios";
import api, { getApiUrl } from "@/lib/api";
import type {
  AuthSession,
  AuthUser,
  LoginCredentials,
  OtpChallenge,
  RegistrationDetails,
} from "@/types/auth";

interface ValidationIssue {
  msg?: string;
  message?: string;
}

interface ErrorPayload {
  detail?: string | ValidationIssue[] | { code?: string; message?: string };
  error?: {
    code?: string;
    message?: string;
    details?: ValidationIssue[];
  };
  message?: string;
}

/** The role lives on each workspace (AuthWorkspace.role), never on the user. */
function userFromSession(session: AuthSession): AuthUser {
  return session.user;
}

export async function getCurrentSession() {
  const response = await api.get<AuthSession>("/auth/me");
  return response.data;
}

export async function getCurrentUser() {
  return userFromSession(await getCurrentSession());
}

export async function loginSession(credentials: LoginCredentials) {
  const response = await api.post<AuthSession>("/auth/login", {
    identifier: credentials.email,
    password: credentials.password,
  });
  return response.data;
}

export async function login(credentials: LoginCredentials) {
  return userFromSession(await loginSession(credentials));
}

export async function logout() {
  await api.post("/auth/logout");
}

export async function register(details: RegistrationDetails) {
  await api.post("/auth/register", {
    display_name: details.name,
    email: details.email,
    password: details.password,
    locale: details.locale ?? "fa",
  });
}

export async function verifyEmail(token: string) {
  await api.post("/auth/verify-email", { token });
}

/** POST /auth/verify-email/resend (session + CSRF, gap G2). */
export async function resendVerificationEmail() {
  const response = await api.post<{ status: "accepted" | "already_verified"; resend_after: number }>(
    "/auth/verify-email/resend",
  );
  return response.data;
}

export async function requestPasswordReset(email: string) {
  await api.post("/auth/forgot-password", { email });
}

export async function resetPassword(token: string, password: string) {
  await api.post("/auth/reset-password", { token, password });
}

export async function requestPhoneOtp(phoneNumber: string) {
  const response = await api.post<OtpChallenge>("/auth/otp/request", { phone: phoneNumber });
  return response.data;
}

export async function verifyPhoneOtp(challengeId: string, code: string) {
  const response = await api.post<AuthSession>("/auth/otp/verify", {
    challenge_id: challengeId,
    code,
  });
  return userFromSession(response.data);
}

export function startGoogleSignIn() {
  window.location.assign(getApiUrl("/auth/google/start"));
}

export function getAuthErrorMessage(error: unknown, fallback: string) {
  if (!isAxiosError<ErrorPayload>(error)) return fallback;

  const apiError = error.response?.data?.error;
  if (apiError?.details?.length) {
    const issue = apiError.details.find((item) => item.message || item.msg);
    const issueMessage = issue?.message || issue?.msg;
    if (issueMessage) return issueMessage;
  }
  if (apiError?.message?.trim()) return apiError.message;

  const detail = error.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message?.trim()) {
    return detail.message;
  }
  if (Array.isArray(detail)) {
    const message = detail.find((issue) => issue.msg)?.msg;
    if (message) return message;
  }

  const message = error.response?.data?.message;
  return typeof message === "string" && message.trim() ? message : fallback;
}
