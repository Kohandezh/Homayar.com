import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";

/**
 * Which sign-in methods this deployment offers, as the API reports them
 * (GET /auth/methods, gap G1). A method is offered only when the server says
 * it is enabled *and* configured; while the answer is loading or when it
 * cannot be read, only email and password are offered. There is no build
 * flag: the console cannot drift from the backend configuration.
 */
export interface AuthMethods {
  password: boolean;
  google: boolean;
  phone: boolean;
}

export const AUTH_METHODS_QUERY_KEY = ["auth", "methods"] as const;

const PASSWORD_ONLY: AuthMethods = { password: true, google: false, phone: false };

interface AuthMethodsResponse {
  password?: unknown;
  google?: unknown;
  phone_otp?: unknown;
}

export async function getAuthMethods(): Promise<AuthMethods> {
  const response = await api.get<AuthMethodsResponse>("/auth/methods");
  return {
    password: response.data.password !== false,
    google: response.data.google === true,
    phone: response.data.phone_otp === true,
  };
}

export function useAuthMethods(): { methods: AuthMethods; status: "loading" | "ready" | "unavailable" } {
  const query = useQuery({
    queryKey: AUTH_METHODS_QUERY_KEY,
    queryFn: getAuthMethods,
    staleTime: 60_000,
    retry: 1,
  });
  if (query.data) return { methods: query.data, status: "ready" };
  return { methods: PASSWORD_ONLY, status: query.isError ? "unavailable" : "loading" };
}
