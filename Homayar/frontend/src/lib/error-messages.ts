import { apiErrorInfo, minutesUntil } from "@/lib/api-errors";
import { formatNumber } from "@/lib/format";

type Lang = "fa" | "en";

/** Page-specific texts for API error codes, in both console languages. */
export type ErrorCodeMessages = Record<string, { fa: string; en: string }>;

/**
 * What kind of failure the user is looking at. It decides the generic text
 * when a page has no specific one; the server's own (English) message is
 * never shown, so internal wording cannot leak into the interface.
 */
export type ApiErrorKind =
  | "authorization"
  | "session"
  | "validation"
  | "rate_limited"
  | "unavailable"
  | "not_found"
  | "unknown";

export interface DescribedError {
  text: string;
  kind: ApiErrorKind;
  code: string | null;
  requestId: string | null;
}

/** Codes the product API uses on every page (backend/security/context.py, main.py). */
const COMMON: ErrorCodeMessages = {
  permission_denied: {
    fa: "دسترسی شما برای این کار کافی نیست یا تغییر کرده است.",
    en: "Your access does not allow this, or it has changed.",
  },
  workspace_access_denied: {
    fa: "دسترسی شما به این فضای کاری تغییر کرده است؛ فضای کاری را دوباره انتخاب کنید.",
    en: "Your access to this workspace has changed; choose the workspace again.",
  },
  api_key_scope_denied: {
    fa: "این کار با این کلید API مجاز نیست.",
    en: "This API key does not allow that.",
  },
  csrf_failed: {
    fa: "نشست شما نیاز به تازه‌سازی دارد؛ صفحه را دوباره بارگذاری کنید.",
    en: "Your session needs refreshing; reload the page.",
  },
  authentication_required: {
    fa: "نشست شما پایان یافته است؛ دوباره وارد شوید.",
    en: "Your session has ended; sign in again.",
  },
  validation_error: {
    fa: "مقدار واردشده پذیرفته نشد؛ فیلدها را بررسی کنید.",
    en: "The values were not accepted; check the fields.",
  },
  rate_limiter_unavailable: {
    fa: "این کار الان در دسترس نیست؛ کمی بعد دوباره تلاش کنید.",
    en: "This is not available right now; try again shortly.",
  },
};

const GENERIC = {
  network: {
    fa: "ارتباط با سرور برقرار نشد؛ اتصال را بررسی کنید و دوباره تلاش کنید.",
    en: "The server could not be reached; check the connection and try again.",
  },
  unavailable: {
    fa: "سرویس الان در دسترس نیست؛ کمی بعد دوباره تلاش کنید.",
    en: "The service is not available right now; try again shortly.",
  },
  rateLimited: {
    fa: (minutes: string) => `درخواست‌ها زیاد بوده است؛ ${minutes} دقیقهٔ دیگر دوباره تلاش کنید.`,
    en: (minutes: string) => `Too many requests; try again in ${minutes} minute(s).`,
  },
  rateLimitedNow: {
    fa: "درخواست‌ها زیاد بوده است؛ کمی بعد دوباره تلاش کنید.",
    en: "Too many requests; try again shortly.",
  },
  request: {
    fa: "درخواست انجام نشد.",
    en: "The request failed.",
  },
  reference: {
    fa: (id: string) => `شناسهٔ پیگیری: ${id}`,
    en: (id: string) => `Reference: ${id}`,
  },
};

function kindOf(status: number | null, code: string | null, network: boolean): ApiErrorKind {
  if (network) return "unavailable";
  if (code === "authentication_required" || code === "csrf_failed" || status === 401) return "session";
  if (status === 403) return "authorization";
  if (status === 404 || status === 410) return "not_found";
  if (status === 422 || status === 400) return "validation";
  if (status === 429) return "rate_limited";
  if (status !== null && status >= 500) return "unavailable";
  return "unknown";
}

/**
 * A localized message for a failed request, chosen by the API error code.
 * Order: the page's own text for the code, the common text for the code,
 * then a generic text by kind (network, 5xx, 429), then the page fallback
 * with the request id so support can find the server log entry.
 */
export function describeApiError(
  error: unknown,
  options: { lang: Lang; fallback?: string; codes?: ErrorCodeMessages },
): DescribedError {
  const { lang, codes } = options;
  const fallback = options.fallback ?? GENERIC.request[lang];
  const info = apiErrorInfo(error);
  const kind = kindOf(info.status, info.code, info.network);
  const base = { kind, code: info.code, requestId: info.requestId };
  if (info.code && codes?.[info.code]) return { ...base, text: codes[info.code][lang] };
  if (info.code && COMMON[info.code]) return { ...base, text: COMMON[info.code][lang] };
  if (info.network) return { ...base, text: GENERIC.network[lang] };
  if (kind === "rate_limited") {
    return {
      ...base,
      text: info.retryAfterSeconds
        ? GENERIC.rateLimited[lang](formatNumber(minutesUntil(info.retryAfterSeconds), lang))
        : GENERIC.rateLimitedNow[lang],
    };
  }
  if (kind === "unavailable") return { ...base, text: GENERIC.unavailable[lang] };
  const reference = info.requestId ? ` (${GENERIC.reference[lang](info.requestId)})` : "";
  return { ...base, text: `${fallback}${reference}` };
}
