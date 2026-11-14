import { resolvePath } from "react-router";

interface ReturnLocation {
  pathname?: unknown;
  search?: unknown;
  hash?: unknown;
}

const AUTH_PATHS = ["/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/phone"];

/**
 * A pathname as the router matches it: percent escapes decoded, `.` and `..`
 * segments resolved (the router does this for absolute paths, so
 * `/x/../mfa/verify` lands on an MFA page), and the whole string
 * lower-cased. Anything unreadable or unresolvable cannot be a page the app
 * has, so it normalizes to "".
 */
function normalizedPathname(pathname: string): string {
  try {
    return resolvePath(decodeURIComponent(pathname)).pathname.toLowerCase();
  } catch {
    return "";
  }
}

function isMfaNormalized(normalized: string): boolean {
  return normalized === "/mfa" || normalized.startsWith("/mfa/");
}

/** Whether a pathname is an MFA page, however the router spells it (case, escapes). */
export function isMfaPathname(pathname: string): boolean {
  return isMfaNormalized(normalizedPathname(pathname));
}

/**
 * The only pathname shape a return path or `next` may have, after decoding:
 * segments of letters, digits, `_` and `-`, each after one `/` (a trailing
 * `/` is fine). Every console route has this shape (ids are UUIDs). It is an
 * allowlist because a list of bad spellings never ends: dots, `%`, `#`, `?`,
 * white space, control characters (the browser drops a tab or newline),
 * non-ASCII, a backslash and `//` all fail it.
 */
const PLAIN_PATHNAME = /^(\/[A-Za-z0-9_-]+)*\/?$/;

function isInternalPath(pathname: string): boolean {
  let decoded: string;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return false;
  }
  if (!decoded.startsWith("/") || !PLAIN_PATHNAME.test(decoded)) return false;
  // The router matches without case, so the auth and MFA checks read the
  // lower-cased spelling.
  const normalized = decoded.toLowerCase();
  if (AUTH_PATHS.some((path) => normalized === path || normalized.startsWith(`${path}/`))) return false;
  // An MFA page is never a valid destination for an MFA `next` either (no loops).
  return !isMfaNormalized(normalized);
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
