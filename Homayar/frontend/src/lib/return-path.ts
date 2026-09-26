interface ReturnLocation {
  pathname?: unknown;
  search?: unknown;
  hash?: unknown;
}

const AUTH_PATHS = ["/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/phone"];

/**
 * Where to go after signing in: the in-app location RequireAuth recorded in
 * the router state, or the overview. Only a path inside this application is
 * accepted, never another origin or an authentication page.
 */
export function safeReturnPath(state: unknown): string {
  const from = (state as { from?: ReturnLocation } | null)?.from;
  const pathname = typeof from?.pathname === "string" ? from.pathname : "";
  if (!pathname.startsWith("/") || pathname.startsWith("//") || pathname.includes("\\")) return "/";
  if (AUTH_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) return "/";
  const search = typeof from?.search === "string" && from.search.startsWith("?") ? from.search : "";
  const hash = typeof from?.hash === "string" && from.hash.startsWith("#") ? from.hash : "";
  return `${pathname}${search}${hash}`;
}
