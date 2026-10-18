/**
 * Pure rules of the API keys page: the fixed scope catalog (K4, mirrored from
 * the backend), the status and expiry-warning rules of the list (K2), and the
 * POST body of the create form (K3). No React here, so every rule can be
 * tested on its own.
 */
import { API_BASE_URL } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import type { Lang } from "@/lib/i18n-context";
import { readinessBaseUrl } from "@/lib/platform-api";
import type { ApiKey, WorkspaceMember } from "@/types/platform";

export type ApiKeyScope =
  | "meeting:read"
  | "meeting:create"
  | "meeting:update"
  | "member:read"
  | "team:read"
  | "billing:read"
  | "audit:read";

/** The fixed order and texts of K4; must equal the backend's API_KEY_SCOPES. */
export interface ApiKeyScopeInfo {
  scope: ApiKeyScope;
  en: string;
  fa: string;
}

export const API_KEY_SCOPES: readonly ApiKeyScopeInfo[] = [
  { scope: "meeting:read", en: "Read meetings, transcripts and summaries", fa: "خواندن جلسه‌ها، متن‌ها و خلاصه‌ها" },
  { scope: "meeting:create", en: "Upload new meetings", fa: "بارگذاری جلسهٔ جدید" },
  { scope: "meeting:update", en: "Change meetings, such as titles and action items", fa: "تغییر جلسه‌ها، مانند عنوان و اقدام‌ها" },
  { scope: "member:read", en: "See the member list", fa: "دیدن فهرست اعضا" },
  { scope: "team:read", en: "See teams", fa: "دیدن تیم‌ها" },
  { scope: "billing:read", en: "See usage and subscription", fa: "دیدن مصرف و اشتراک" },
  { scope: "audit:read", en: "Read the audit log", fa: "خواندن گزارش رویدادها" },
];

const SCOPE_ORDER: ReadonlyMap<string, number> = new Map(
  API_KEY_SCOPES.map((entry, index) => [entry.scope, index]),
);

export const NAME_MAX_LENGTH = 160;

/** The server's `api_key_max_ttl_days` is 365; the console never offers more. */
export const EXPIRY_CHOICES = [
  { days: 30, en: "30 days", fa: "۳۰ روز" },
  { days: 90, en: "90 days", fa: "۹۰ روز" },
  { days: 180, en: "180 days", fa: "۱۸۰ روز" },
  { days: 365, en: "1 year", fa: "۱ سال" },
] as const;

export const DEFAULT_EXPIRY_DAYS = 90;

/** An active key warns about its expiry with this many days left. */
export const EXPIRY_WARNING_DAYS = 14;

const DAY_MS = 86_400_000;

export type ApiKeyStatus = "active" | "expired" | "revoked";

/**
 * The status of a key, decided in the SPEC's order: a revocation always wins
 * over an expiry, and `expires_at == now` already counts as expired.
 */
export function apiKeyStatus(
  key: Pick<ApiKey, "revoked_at" | "expires_at">,
  now = Date.now(),
): ApiKeyStatus {
  if (key.revoked_at) return "revoked";
  if (key.expires_at && Date.parse(key.expires_at) <= now) return "expired";
  return "active";
}

export function statusLabel(status: ApiKeyStatus, lang: Lang): string {
  const labels: Record<ApiKeyStatus, Record<Lang, string>> = {
    active: { en: "Active", fa: "فعال" },
    expired: { en: "Expired", fa: "منقضی‌شده" },
    revoked: { en: "Revoked", fa: "باطل‌شده" },
  };
  return labels[status][lang];
}

/**
 * The whole days left on an active key's expiry, rounded up, while fourteen
 * or fewer remain; null when there is nothing to warn about (no expiry, more
 * than fourteen days, or a key that is already expired or revoked).
 */
export function expiryWarningDays(
  key: Pick<ApiKey, "revoked_at" | "expires_at">,
  now = Date.now(),
): number | null {
  if (apiKeyStatus(key, now) !== "active" || !key.expires_at) return null;
  const msLeft = Date.parse(key.expires_at) - now;
  if (msLeft <= 0) return null;
  const days = Math.ceil(msLeft / DAY_MS);
  return days <= EXPIRY_WARNING_DAYS ? days : null;
}

export function expiryWarningLabel(days: number, lang: Lang): string {
  if (lang === "fa") return `${formatNumber(days, lang)} روز تا انقضا`;
  return days === 1 ? "Expires in 1 day" : `Expires in ${formatNumber(days, lang)} days`;
}

/** Known scopes in K4 order; unknown ones last, in the server's own order. */
export function orderedScopes(scopes: readonly string[]): string[] {
  const known = scopes.filter((scope) => SCOPE_ORDER.has(scope)).sort(
    (a, b) => SCOPE_ORDER.get(a)! - SCOPE_ORDER.get(b)!,
  );
  const unknown = scopes.filter((scope) => !SCOPE_ORDER.has(scope));
  return [...known, ...unknown];
}

/** Exactly the POST /api-keys body of K3: trimmed name, K4-ordered scopes, days. */
export function createPostBody(name: string, scopes: readonly string[], expires_in_days: number) {
  return { name: name.trim(), scopes: orderedScopes(scopes), expires_in_days };
}

/**
 * The creator shown in the list when a member matches: the first non-empty of
 * display name, email, phone, then the id (the same chain as AuditLogPage and
 * MembersPage). A matching member never reads as "Former member"; that text is
 * the page's answer when no member matches at all (A1).
 */
export function creatorLabel(
  member: Pick<WorkspaceMember, "user_id" | "display_name" | "email" | "phone_e164">,
): string {
  return member.display_name?.trim() || member.email || member.phone_e164 || member.user_id;
}

/**
 * The API docs URL: the API base without its version part plus `/docs`. The
 * default `/api/v1` base becomes exactly `/api/docs` (the backend serves
 * Swagger there when its environment is not production).
 */
export function apiDocsUrl(apiBaseUrl: string = API_BASE_URL): string {
  return `${readinessBaseUrl(apiBaseUrl)}/api/docs`;
}
