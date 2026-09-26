import { isCancel } from "axios";
import { apiErrorInfo } from "@/lib/api-errors";

export type UploadFailureKind =
  | "network"
  | "cancelled"
  | "too_large"
  | "too_long"
  | "unsupported_type"
  | "unreadable"
  | "bad_filename"
  | "verification_required"
  | "forbidden"
  | "conflict"
  | "duplicate"
  | "target_deleted"
  | "quota"
  | "concurrency"
  | "rate_limited"
  | "temporary"
  | "unknown";

/**
 * How the same upload may be tried again.
 * - `same_key`: resend the same file with the same Idempotency-Key, so a
 *   request the server already accepted replays instead of creating a second
 *   meeting, job or usage reservation.
 * - `new_file`: the bytes or the upload identity were refused; the user must
 *   choose a file again (which starts a new identity).
 * - `none`: retrying cannot help (permission, verification, allowance).
 */
export type UploadRetry = "same_key" | "new_file" | "none";

export interface QuotaDetails {
  metric: string | null;
  limit: number | null;
  used: number | null;
  reserved: number | null;
  requested: number | null;
  remaining: number | null;
}

export interface UploadFailure {
  kind: UploadFailureKind;
  retry: UploadRetry;
  /** Seconds the server asked to wait (Retry-After), if any. */
  retryAfterSeconds: number | null;
  quota: QuotaDetails | null;
  /** Existing meeting for a duplicate upload, when the server named it. */
  meetingId: string | null;
  code: string | null;
}

const TOO_LARGE = new Set(["upload_too_large", "media_file_too_large"]);
const UNSUPPORTED = new Set([
  "unsupported_extension",
  "unsupported_mime_type",
  "mime_type_mismatch",
  "unsupported_container",
  "extension_content_mismatch",
]);
const UNREADABLE = new Set([
  "empty_media",
  "empty_upload",
  "missing_audio_stream",
  "invalid_audio_stream",
  "invalid_media_duration",
  "media_duration_implausible",
  "malformed_media",
  "media_decode_failed",
  "invalid_probe_metadata",
  "media_file_changed",
]);
const BAD_FILENAME = new Set(["unsafe_filename", "missing_filename"]);

function numberOrNull(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function quotaDetails(details: unknown): QuotaDetails | null {
  if (!details || typeof details !== "object" || Array.isArray(details)) return null;
  const record = details as Record<string, unknown>;
  return {
    metric: typeof record.metric === "string" ? record.metric : null,
    limit: numberOrNull(record.limit),
    used: numberOrNull(record.used),
    reserved: numberOrNull(record.reserved),
    requested: numberOrNull(record.requested),
    remaining: numberOrNull(record.remaining),
  };
}

function failure(
  kind: UploadFailureKind,
  retry: UploadRetry,
  extra: Partial<UploadFailure> = {},
): UploadFailure {
  return { kind, retry, retryAfterSeconds: null, quota: null, meetingId: null, code: null, ...extra };
}

/** Classifies a failed `POST /uploads` by the product's documented refusal codes. */
export function classifyUploadFailure(error: unknown): UploadFailure {
  if (isCancel(error)) return failure("cancelled", "same_key");
  const info = apiErrorInfo(error);
  const code = info.code;
  const base = { code, retryAfterSeconds: info.retryAfterSeconds };
  if (info.network) return failure("network", "same_key", base);
  if (code && TOO_LARGE.has(code)) return failure("too_large", "new_file", base);
  if (code === "media_duration_exceeded") return failure("too_long", "new_file", base);
  if (code && UNSUPPORTED.has(code)) return failure("unsupported_type", "new_file", base);
  if (code && UNREADABLE.has(code)) return failure("unreadable", "new_file", base);
  if (code && BAD_FILENAME.has(code)) return failure("bad_filename", "new_file", base);
  switch (code) {
    case "verified_identity_required":
      return failure("verification_required", "none", base);
    case "permission_denied":
    case "workspace_access_denied":
      return failure("forbidden", "none", base);
    case "ingestion_conflict":
      return failure("conflict", "new_file", base);
    case "duplicate_media": {
      const details = info.details as { meeting_id?: unknown } | null;
      const meetingId = typeof details?.meeting_id === "string" ? details.meeting_id : null;
      return failure("duplicate", "none", { ...base, meetingId });
    }
    case "ingestion_target_deleted":
      return failure("target_deleted", "new_file", base);
    case "audio_quota_exceeded":
      return failure("quota", "none", { ...base, quota: quotaDetails(info.details) });
    case "concurrent_job_limit_reached":
      return failure("concurrency", "same_key", { ...base, quota: quotaDetails(info.details) });
    case "upload_rate_limited":
      return failure("rate_limited", "same_key", base);
  }
  // A proxy may refuse an oversized body before the API sees it (no JSON code).
  if (info.status === 413) return failure("too_large", "new_file", base);
  // nginx's own upload rate limit (2 per minute per address) answers 429
  // without a code or Retry-After: the same file can be sent again later.
  if (info.status === 429) return failure("rate_limited", "same_key", base);
  if (info.status !== null && info.status >= 500) {
    return failure("temporary", "same_key", { ...base, retryAfterSeconds: info.retryAfterSeconds });
  }
  return failure("unknown", "same_key", base);
}
