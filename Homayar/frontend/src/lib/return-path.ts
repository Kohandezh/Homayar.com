interface ReturnLocation {
  pathname?: unknown;
  search?: unknown;
  hash?: unknown;
}

const AUTH_PATHS = ["/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/phone"];

function isInternalPath(pathname: string): boolean {
  if (!pathname.startsWith("/") || pathname.startsWith("//") || pathname.includes("\\")) return false;
  if (AUTH_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`))) return false;
  // An MFA page is never a valid destination for an MFA `next` either (no loops).
  if (pathname === "/mfa" || pathname.startsWith("/mfa/")) return false;
  return true;
}

/**
 * Where to go after signing in: the in-app location RequireAuth recorded in
 * the router state, or the overview. Only a path inside this application is
 * accepted, never another origin or an authentication page.
 */
export function safeReturnPath(state: unknown): string {
  const from = (state as { from?: ReturnLocation } | null)?.from;
  const pathname = typeof from?.pathname === "string" ? from.pathname : "";
  if (!isInternalPath(pathname)) return "/";
  const search = typeof from?.search === "string" && from.search.startsWith("?") ? from.search : "";
  const hash = typeof from?.hash === "string" && from.hash.startsWith("#") ? from.hash : "";
  return `${pathname}${search}${hash}`;
}

/**
 * The `next` query parameter of an MFA page: where to go once MFA is done.
 * Same rule as `safeReturnPath` (an internal console path, never an
 * authentication page), plus never an MFA page itself, so a redirect cannot
 * loop. The value keeps its own search and hash; anything else becomes "/".
 */
export function safeNextPath(raw: unknown): string {
  if (typeof raw !== "string" || raw.length === 0) return "/";
  const match = raw.match(/^([^?#]*)([?#].*)?$/);
  const pathname = match?.[1] ?? "";
  if (!isInternalPath(pathname)) return "/";
  return `${pathname}${match?.[2] ?? ""}`;
}
