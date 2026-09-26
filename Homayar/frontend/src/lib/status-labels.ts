/**
 * Labels for the state enumerations the API returns (meeting status, job
 * state, stage state, quality/evidence states, action and share states).
 * Values come from backend/database/saas_models.py, the quality schema,
 * invitation states and audit outcomes. An unknown value is shown as sent,
 * never guessed (and never styled as a success).
 */
const LABELS: Record<string, { fa: string; en: string }> = {
  uploading: { fa: "در حال ارسال", en: "Uploading" },
  uploaded: { fa: "ارسال‌شده", en: "Uploaded" },
  queued: { fa: "در صف پردازش", en: "Queued" },
  processing: { fa: "در حال پردازش", en: "Processing" },
  transcribing: { fa: "تبدیل گفتار به متن", en: "Transcribing" },
  diarizing: { fa: "تفکیک گوینده‌ها", en: "Separating speakers" },
  analyzing: { fa: "در حال تحلیل", en: "Analyzing" },
  completed: { fa: "تکمیل‌شده", en: "Completed" },
  partial: { fa: "ناقص", en: "Partial" },
  failed: { fa: "ناموفق", en: "Failed" },
  cancelled: { fa: "لغوشده", en: "Cancelled" },
  running: { fa: "در حال اجرا", en: "Running" },
  dead_letter: { fa: "متوقف؛ نیازمند بررسی", en: "Stopped; needs review" },
  unavailable: { fa: "در دسترس نیست", en: "Unavailable" },
  insufficient_evidence: { fa: "شواهد ناکافی", en: "Insufficient evidence" },
  skipped: { fa: "انجام نشد", en: "Skipped" },
  complete: { fa: "کامل", en: "Complete" },
  verified: { fa: "تأییدشده", en: "Verified" },
  inferred: { fa: "استنباطی", en: "Inferred" },
  unknown: { fa: "نامشخص", en: "Unknown" },
  sufficient: { fa: "کافی", en: "Sufficient" },
  insufficient: { fa: "ناکافی", en: "Insufficient" },
  open: { fa: "باز", en: "Open" },
  in_progress: { fa: "در جریان", en: "In progress" },
  done: { fa: "انجام‌شده", en: "Done" },
  active: { fa: "فعال", en: "Active" },
  trialing: { fa: "دورهٔ آزمایشی", en: "Trial" },
  past_due: { fa: "سررسید گذشته", en: "Past due" },
  expired: { fa: "منقضی", en: "Expired" },
  revoked: { fa: "لغوشده", en: "Revoked" },
  // Invitation states (backend InvitationStatus) and audit outcomes.
  pending: { fa: "در انتظار پذیرش", en: "Pending" },
  accepted: { fa: "پذیرفته‌شده", en: "Accepted" },
  success: { fa: "موفق", en: "Succeeded" },
  failure: { fa: "ناموفق", en: "Failed" },
  low: { fa: "کم", en: "Low" },
  medium: { fa: "متوسط", en: "Medium" },
  high: { fa: "زیاد", en: "High" },
};

export function statusLabel(value: string, lang: "fa" | "en") {
  return LABELS[value]?.[lang] ?? value.replaceAll("_", " ");
}

export const NONTERMINAL_MEETING_STATUSES = [
  "uploading",
  "uploaded",
  "queued",
  "processing",
  "transcribing",
  "diarizing",
  "analyzing",
] as const;
