import { isAxiosError } from "axios";

/**
 * What the console needs to know about a failed API call.
 *
 * The product API answers `{"error": {"code", "message", "request_id", "details?"}}`
 * (backend/main.py). The code, not the English message, decides what the
 * console shows; the server message is kept only as a last-resort detail.
 */
export interface ApiErrorInfo {
  /** HTTP status, or null when no response arrived (network, abort, CORS). */
  status: number | null;
  code: string | null;
  message: string | null;
  requestId: string | null;
  /** Seconds from the Retry-After header (delta-seconds or HTTP date). */
  retryAfterSeconds: number | null;
  details: unknown;
  network: boolean;
}

interface ErrorBody {
  error?: { code?: unknown; message?: unknown; request_id?: unknown; details?: unknown };
  detail?: unknown;
}

function headerValue(headers: unknown, name: string): string | null {
  if (!headers || typeof headers !== "object") return null;
  const record = headers as Record<string, unknown> & { get?: (key: string) => unknown };
  const raw = typeof record.get === "function" ? record.get(name) : record[name] ?? record[name.toLowerCase()];
  if (raw === undefined || raw === null) return null;
  return Array.isArray(raw) ? String(raw[0]) : String(raw);
}

export function parseRetryAfter(value: string | null, now = Date.now()): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed);
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return null;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

export function apiErrorInfo(error: unknown): ApiErrorInfo {
  if (!isAxiosError<ErrorBody>(error)) {
    return {
      status: null,
      code: null,
      message: null,
      requestId: null,
      retryAfterSeconds: null,
      details: null,
      network: false,
    };
  }
  const response = error.response;
  const body = response?.data;
  const envelope = body && typeof body === "object" ? body.error : undefined;
  const legacy = body && typeof body === "object" && body.detail && typeof body.detail === "object"
    ? (body.detail as { code?: unknown; message?: unknown })
    : undefined;
  const code = envelope?.code ?? legacy?.code;
  const message = envelope?.message ?? legacy?.message;
  return {
    status: response?.status ?? null,
    code: typeof code === "string" ? code : null,
    message: typeof message === "string" ? message : null,
    requestId: typeof envelope?.request_id === "string" ? envelope.request_id : null,
    retryAfterSeconds: parseRetryAfter(headerValue(response?.headers, "retry-after")),
    details: envelope?.details ?? null,
    network: !response,
  };
}

/** Whole minutes (rounded up) for a user-facing "try again in" message. */
export function minutesUntil(seconds: number): number {
  return Math.max(1, Math.ceil(seconds / 60));
}
