import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";

export const AUTH_EXPIRED_EVENT = "homayar:auth-expired";
/** A 403 that may mean the session's workspace access or role changed. */
export const WORKSPACE_DENIED_EVENT = "homayar:workspace-denied";

export interface WorkspaceDeniedDetail {
  code: "workspace_access_denied" | "permission_denied";
  /** The X-Workspace-ID the refused request carried, if any. */
  workspaceId: string | null;
}
export const API_BASE_URL = (import.meta.env.VITE_API_URL || "/api/v1").replace(/\/+$/, "");

const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { Accept: "application/json" },
});

const refreshClient = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: { Accept: "application/json" },
});

const CSRF_COOKIE_NAME = "homayar_csrf";
export const WORKSPACE_STORAGE_KEY = "homayar_workspace_id";

export function getCsrfToken() {
  if (typeof document === "undefined") return null;
  const prefix = `${CSRF_COOKIE_NAME}=`;
  const encoded = document.cookie
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length);
  return encoded ? decodeURIComponent(encoded) : null;
}

function attachCsrfHeader(config: InternalAxiosRequestConfig) {
  const method = config.method?.toLowerCase();
  const requestPath = config.url?.split("?")[0] ?? "";
  if (!requestPath.startsWith("/auth/") && typeof window !== "undefined") {
    const workspaceId = window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
    if (workspaceId) config.headers.set("X-Workspace-ID", workspaceId);
  }
  if (method && !["get", "head", "options"].includes(method)) {
    const token = getCsrfToken();
    if (token) config.headers.set("X-CSRF-Token", token);
  }
  return config;
}

api.interceptors.request.use(attachCsrfHeader);
refreshClient.interceptors.request.use(attachCsrfHeader);

const publicAuthPaths = new Set([
  "/auth/login",
  "/auth/register",
  "/auth/logout",
  "/auth/refresh",
  "/auth/verify-email",
  "/auth/forgot-password",
  "/auth/reset-password",
  "/auth/otp/request",
  "/auth/otp/verify",
]);

interface RetriableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

let refreshRequest: Promise<unknown> | null = null;

function emitAuthExpired() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}

function emitWorkspaceDenied(error: AxiosError, config: InternalAxiosRequestConfig | undefined) {
  if (typeof window === "undefined" || error.response?.status !== 403) return;
  const code = (error.response.data as { error?: { code?: unknown } } | undefined)?.error?.code;
  if (code !== "workspace_access_denied" && code !== "permission_denied") return;
  const header = config?.headers?.get?.("X-Workspace-ID");
  const detail: WorkspaceDeniedDetail = {
    code,
    workspaceId: typeof header === "string" && header ? header : null,
  };
  window.dispatchEvent(new CustomEvent(WORKSPACE_DENIED_EVENT, { detail }));
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const config = error.config as RetriableRequestConfig | undefined;
    const requestPath = config?.url?.split("?")[0] ?? "";

    if (
      error.response?.status === 401 &&
      config &&
      !config._retry &&
      !publicAuthPaths.has(requestPath)
    ) {
      config._retry = true;

      try {
        refreshRequest ??= refreshClient.post("/auth/refresh").finally(() => {
          refreshRequest = null;
        });
        await refreshRequest;
        return api(config);
      } catch {
        emitAuthExpired();
      }
    }

    emitWorkspaceDenied(error, config);
    return Promise.reject(error);
  }
);

export function getApiUrl(path: string) {
  return `${API_BASE_URL}/${path.replace(/^\/+/, "")}`;
}

export default api;
