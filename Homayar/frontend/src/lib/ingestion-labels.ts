/**
 * Labels for the ingestion fields the ledger shows: the source type, the
 * status, and the media refusal codes a rejected row can carry. Values come
 * from backend/routers/v1/ingestions.py and the refusal code lists of
 * upload-errors.ts. An unknown value is shown as sent, never guessed.
 */
type Lang = "fa" | "en";

const SOURCE_TYPE_LABELS: Record<string, { fa: string; en: string }> = {
  file_upload: { fa: "بارگذاری فایل", en: "File upload" },
  microphone: { fa: "میکروفون", en: "Microphone" },
  system_audio: { fa: "صدای سیستم", en: "System audio" },
  mic_plus_system_audio: { fa: "میکروفون و صدای سیستم", en: "Microphone and system audio" },
  sip_voip: { fa: "SIP / VoIP", en: "SIP / VoIP" },
  meeting_bot: { fa: "ربات جلسه", en: "Meeting bot" },
  external_recording_url: { fa: "پیوند ضبط", en: "Recording link" },
  offline_folder_watcher: { fa: "پوشهٔ پایش‌شده", en: "Watched folder" },
  api: { fa: "API", en: "API" },
  voicemail: { fa: "پیام صوتی", en: "Voicemail" },
};

export function ingestionSourceLabel(sourceType: string, lang: Lang): string {
  return SOURCE_TYPE_LABELS[sourceType]?.[lang] ?? sourceType;
}

const STATUS_LABELS: Record<string, { fa: string; en: string }> = {
  accepted: { fa: "پذیرفته‌شده", en: "Accepted" },
  rejected: { fa: "ردشده", en: "Rejected" },
};

export function ingestionStatusLabel(status: string, lang: Lang): string {
  return STATUS_LABELS[status]?.[lang] ?? status;
}

/**
 * The refusal codes the backend writes on a rejected row, grouped as in
 * upload-errors.ts (whose lists are private to the upload flow, so the codes
 * are copied here rather than imported).
 */
const REASON_LABELS: Record<string, { fa: string; en: string }> = {
  upload_too_large: { fa: "فایل بیش از حد بزرگ است", en: "File too large" },
  media_file_too_large: { fa: "فایل بیش از حد بزرگ است", en: "File too large" },
  media_duration_exceeded: { fa: "ضبط بیش از حد طولانی است", en: "Recording too long" },
  unsupported_extension: { fa: "نوع فایل پشتیبانی نمی‌شود", en: "Unsupported file type" },
  unsupported_mime_type: { fa: "نوع فایل پشتیبانی نمی‌شود", en: "Unsupported file type" },
  mime_type_mismatch: { fa: "نوع فایل پشتیبانی نمی‌شود", en: "Unsupported file type" },
  unsupported_container: { fa: "نوع فایل پشتیبانی نمی‌شود", en: "Unsupported file type" },
  extension_content_mismatch: { fa: "نوع فایل پشتیبانی نمی‌شود", en: "Unsupported file type" },
  empty_media: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  empty_upload: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  missing_audio_stream: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  invalid_audio_stream: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  invalid_media_duration: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  media_duration_implausible: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  malformed_media: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  media_decode_failed: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  invalid_probe_metadata: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  media_file_changed: { fa: "ضبط قابل خواندن نیست", en: "Unreadable recording" },
  unsafe_filename: { fa: "نام فایل نامعتبر است", en: "Invalid file name" },
  missing_filename: { fa: "نام فایل نامعتبر است", en: "Invalid file name" },
};

export function ingestionReasonLabel(reason: string, lang: Lang): string {
  return REASON_LABELS[reason]?.[lang] ?? reason;
}
