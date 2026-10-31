/**
 * Test-only fixtures for console behaviour tests. Nothing here is imported by
 * application code; every API answer below is a hand-written stand-in for the
 * product contract recorded in docs/handoff/session-b/CONTRACT_NOTE.md.
 */
import type { ReactElement, ReactNode } from "react";
import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, AxiosHeaders, type AxiosResponse } from "axios";
import { MemoryRouter, type MemoryRouterProps } from "react-router";
import { Toast } from "@heroui/react";
import { vi } from "vitest";
import { I18nProvider } from "@/lib/i18n";
import { AuthContext, type AuthContextValue } from "@/lib/auth-context";
import { AUTH_METHODS_QUERY_KEY, type AuthMethods } from "@/lib/auth-methods";
import type { AuthUser, AuthWorkspace } from "@/types/auth";

export const FIXTURE_USER: AuthUser = {
  id: "user-1",
  email: "person@example.com",
  phone_e164: null,
  display_name: "Person",
  locale: "fa",
  email_verified: true,
  phone_verified: false,
  is_active: true,
};

/** FIXTURE_USER as a platform operator (`system_role` from HOM-184). */
export const FIXTURE_OPERATOR: AuthUser = { ...FIXTURE_USER, system_role: "operator" };

export const FIXTURE_WORKSPACE: AuthWorkspace = {
  id: "ws-1",
  organization_id: "org-1",
  name: "Fixture workspace",
  role: "owner",
};

export function authContext(overrides: Partial<AuthContextValue> = {}): AuthContextValue {
  return {
    user: null,
    workspaces: [],
    workspace: null,
    status: "anonymous",
    endReason: null,
    workspaceNotice: null,
    unavailableWorkspaceIds: [],
    signIn: vi.fn<AuthContextValue["signIn"]>(),
    signOut: vi.fn<AuthContextValue["signOut"]>(),
    refreshUser: vi.fn<AuthContextValue["refreshUser"]>(),
    retrySession: vi.fn<AuthContextValue["retrySession"]>(),
    setWorkspace: vi.fn<AuthContextValue["setWorkspace"]>(),
    ...overrides,
  };
}

/** An axios error shaped like the product API's `{"error": {...}}` envelope. */
export function apiError(
  status: number,
  code: string,
  { headers = {}, details }: { headers?: Record<string, string>; details?: unknown } = {},
): AxiosError {
  const config = { headers: new AxiosHeaders() };
  const response: AxiosResponse = {
    status,
    statusText: String(status),
    headers: new AxiosHeaders(headers),
    config,
    data: { error: { code, message: `fixture ${code}`, request_id: "req-fixture", details } },
  };
  return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_RESPONSE", config, {}, response);
}

export function networkError(): AxiosError {
  return new AxiosError("Network Error", "ERR_NETWORK", { headers: new AxiosHeaders() }, {});
}

export function okResponse<T>(data: T, status = 200): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: "OK",
    headers: new AxiosHeaders(),
    config: { headers: new AxiosHeaders() },
  };
}

type InitialEntry = NonNullable<MemoryRouterProps["initialEntries"]>[number];

interface RenderOptions {
  route?: InitialEntry;
  auth?: Partial<AuthContextValue>;
  lang?: "fa" | "en";
  queryClient?: QueryClient;
}

export function testQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

/** A query client that already holds the server's GET /auth/methods answer. */
export function withAuthMethods(methods: Partial<AuthMethods>) {
  const client = testQueryClient();
  client.setQueryData(AUTH_METHODS_QUERY_KEY, {
    password: true,
    google: false,
    phone: false,
    registration: true,
    ...methods,
  });
  return client;
}

/** Renders inside the console's providers with a fixture auth context. */
export function renderConsole(ui: ReactElement, options: RenderOptions = {}) {
  if (options.lang === "en") window.localStorage.setItem("homayar_lang", "en");
  const queryClient = options.queryClient ?? testQueryClient();
  const context = authContext(options.auth);
  function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider>
          {/* Mirrors App.tsx, so tests run the same router mode. */}
          <MemoryRouter initialEntries={[options.route ?? "/"]} useTransitions={false}>
            <AuthContext.Provider value={context}>
              {children}
              {/* Mirrors App.tsx, so a page's toasts are visible to tests. */}
              <Toast.Provider />
            </AuthContext.Provider>
          </MemoryRouter>
        </I18nProvider>
      </QueryClientProvider>
    );
  }
  return { ...render(ui, { wrapper: Providers }), context, queryClient };
}
