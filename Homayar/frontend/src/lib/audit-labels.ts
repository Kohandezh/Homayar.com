/**
 * Labels for audit log entries (QA S-04). Event types are the ones the
 * backend records (grep of `event_type="…"` in backend/ at the Session C
 * integration). Unknown types, keys and values are shown as sent, never
 * guessed; the raw code stays visible next to a label.
 */
type Lang = "fa" | "en";
type Labels = Record<string, { fa: string; en: string }>;

export const AUDIT_EVENT_LABELS: Labels = {
  "member.invitation.created": { fa: "دعوت عضو ایجاد شد", en: "Member invited" },
  "member.invitation.delivery_failed": { fa: "ارسال ایمیل دعوت ناموفق بود", en: "Invitation email failed" },
  "member.invitation.revoked": { fa: "دعوت لغو شد", en: "Invitation revoked" },
  "member.invitation.accepted": { fa: "دعوت پذیرفته شد", en: "Invitation accepted" },
  "member.role_changed": { fa: "نقش عضو تغییر کرد", en: "Member role changed" },
  "member.removed": { fa: "عضو حذف شد", en: "Member removed" },
  "workspace.settings.updated": { fa: "تنظیمات فضای کاری تغییر کرد", en: "Workspace settings changed" },
  "ai_policy.updated": { fa: "سیاست هوش مصنوعی تغییر کرد", en: "AI policy changed" },
  "meeting.uploaded": { fa: "جلسه بارگذاری شد", en: "Meeting uploaded" },
  "meeting.updated": { fa: "جلسه ویرایش شد", en: "Meeting edited" },
  "meeting.deleted": { fa: "جلسه حذف شد", en: "Meeting deleted" },
  "meeting.exported": { fa: "از جلسه خروجی گرفته شد", en: "Meeting exported" },
  "meeting.read": { fa: "جلسه مشاهده شد", en: "Meeting viewed" },
  "meeting.speaker_renamed": { fa: "نام گوینده تغییر کرد", en: "Speaker renamed" },
  "meeting.share_created": { fa: "لینک اشتراک ساخته شد", en: "Share link created" },
  "meeting.share_revoked": { fa: "لینک اشتراک لغو شد", en: "Share link revoked" },
  "meeting.share_accessed": { fa: "لینک اشتراک باز شد", en: "Share link opened" },
  "meeting.share_recipients_changed": { fa: "گیرندگان لینک اشتراک تغییر کردند", en: "Share recipients changed" },
  "meeting.processing.completed": { fa: "پردازش جلسه تمام شد", en: "Processing finished" },
  "meeting.processing.failed": { fa: "پردازش جلسه ناموفق بود", en: "Processing failed" },
  "meeting.retention_expired": { fa: "مهلت نگهداری جلسه به پایان رسید", en: "Retention period ended" },
  "meeting.recording.deleted_after_processing": { fa: "فایل صوتی پس از پردازش حذف شد", en: "Recording deleted after processing" },
  "meeting.recording.retained_after_processing": { fa: "فایل صوتی پس از پردازش نگه داشته شد", en: "Recording kept after processing" },
  "meeting.purge_completed": { fa: "حذف کامل جلسه انجام شد", en: "Meeting purge completed" },
  "meeting.purge_failed": { fa: "حذف کامل جلسه ناموفق بود", en: "Meeting purge failed" },
  "meeting.purge_requeued": { fa: "حذف کامل جلسه دوباره در صف قرار گرفت", en: "Meeting purge requeued" },
  "meeting.tombstone_reapplied": { fa: "حذف جلسه پس از بازیابی دوباره اعمال شد", en: "Deletion reapplied after restore" },
  "transcript.original_accessed": { fa: "متن اصلی (بدون پوشش) مشاهده شد", en: "Original transcript viewed" },
  "transcript.redaction_created": { fa: "نسخهٔ پوشیدهٔ متن ساخته شد", en: "Redacted transcript created" },
  "transcript.revision.created": { fa: "نسخهٔ تازهٔ متن ساخته شد", en: "Transcript revision created" },
  "pii.revealed": { fa: "یک مقدار پوشیده آشکار شد", en: "Masked value revealed" },
  "subject.exported": { fa: "خروجی دادهٔ یک شخص گرفته شد", en: "Subject data exported" },
  "action_item.created": { fa: "اقدام ساخته شد", en: "Action item created" },
  "action_item.updated": { fa: "اقدام ویرایش شد", en: "Action item edited" },
  "action_item.deleted": { fa: "اقدام حذف شد", en: "Action item deleted" },
  "api_key.created": { fa: "کلید API ساخته شد", en: "API key created" },
  "api_key.revoked": { fa: "کلید API لغو شد", en: "API key revoked" },
  "media.registered": { fa: "فایل رسانه ثبت شد", en: "Media registered" },
  "media.deleted": { fa: "فایل رسانه حذف شد", en: "Media deleted" },
  "media.deletion_requested": { fa: "حذف فایل رسانه درخواست شد", en: "Media deletion requested" },
  "media.integrity_checked": { fa: "سلامت فایل رسانه بررسی شد", en: "Media integrity checked" },
  "media.legal_hold_placed": { fa: "نگهداری قانونی اعمال شد", en: "Legal hold placed" },
  "media.legal_hold_released": { fa: "نگهداری قانونی برداشته شد", en: "Legal hold released" },
  "ingestion.accepted": { fa: "دریافت فایل پذیرفته شد", en: "Ingestion accepted" },
  "ingestion.rejected": { fa: "دریافت فایل رد شد", en: "Ingestion rejected" },
  "ingestion.replayed": { fa: "ارسال تکراری شناسایی شد", en: "Ingestion replayed" },
  "ingestion.conflict": { fa: "تعارض در ارسال فایل", en: "Ingestion conflict" },
  "ingestion.duplicate_media": { fa: "فایل تکراری", en: "Duplicate media" },
  "team.created": { fa: "تیم ساخته شد", en: "Team created" },
  "team.updated": { fa: "تیم ویرایش شد", en: "Team edited" },
  "team.assignment.created": { fa: "عضو به تیم اضافه شد", en: "Team assignment added" },
  "team.assignment.revoked": { fa: "عضو از تیم برداشته شد", en: "Team assignment removed" },
  "auth.register": { fa: "ثبت‌نام", en: "Registration" },
  "auth.email_verified": { fa: "تأیید ایمیل", en: "Email verified" },
  "auth.verification_resent": { fa: "ارسال دوبارهٔ ایمیل تأیید", en: "Verification email resent" },
  "auth.password_reset_requested": { fa: "درخواست بازنشانی گذرواژه", en: "Password reset requested" },
  "auth.password_reset_completed": { fa: "بازنشانی گذرواژه", en: "Password reset" },
  "auth.otp_requested": { fa: "درخواست کد پیامکی", en: "SMS code requested" },
  "auth.otp_verified": { fa: "تأیید کد پیامکی", en: "SMS code verified" },
  "auth.google_completed": { fa: "ورود با Google", en: "Google sign-in" },
};

const DETAIL_KEY_LABELS: Labels = {
  previous_role: { fa: "نقش قبلی", en: "Previous role" },
  new_role: { fa: "نقش جدید", en: "New role" },
  role: { fa: "نقش", en: "Role" },
  fields: { fa: "فیلدهای تغییرکرده", en: "Changed fields" },
  reason: { fa: "دلیل", en: "Reason" },
  format: { fa: "قالب", en: "Format" },
  view: { fa: "نما", en: "View" },
  method: { fa: "روش", en: "Method" },
  purpose: { fa: "هدف", en: "Purpose" },
};

const FIELD_LABELS: Labels = {
  name: { fa: "نام", en: "name" },
  retention_days: { fa: "مدت نگهداری", en: "retention period" },
  retention_mode: { fa: "سیاست نگهداری", en: "retention policy" },
  delete_audio_after_processing: { fa: "حذف صوت پس از پردازش", en: "delete audio after processing" },
};

const TARGET_LABELS: Labels = {
  membership: { fa: "عضویت", en: "Membership" },
  workspace_invitation: { fa: "دعوت", en: "Invitation" },
  workspace: { fa: "فضای کاری", en: "Workspace" },
  meeting: { fa: "جلسه", en: "Meeting" },
  user: { fa: "کاربر", en: "User" },
  share_link: { fa: "لینک اشتراک", en: "Share link" },
  api_key: { fa: "کلید API", en: "API key" },
  action_item: { fa: "اقدام", en: "Action item" },
  media: { fa: "فایل رسانه", en: "Media" },
  ingestion: { fa: "دریافت فایل", en: "Ingestion" },
  ai_policy: { fa: "سیاست هوش مصنوعی", en: "AI policy" },
  entitlement: { fa: "سهمیه", en: "Entitlement" },
  meeting_speaker: { fa: "گوینده", en: "Speaker" },
  team: { fa: "تیم", en: "Team" },
  team_assignment: { fa: "عضویت تیم", en: "Team assignment" },
  transcript_revision: { fa: "نسخهٔ متن", en: "Transcript revision" },
  phone: { fa: "شمارهٔ تلفن", en: "Phone" },
  organization_membership: { fa: "عضویت سازمان", en: "Organization membership" },
};

const ROLE_VALUES: Labels = {
  owner: { fa: "مالک", en: "Owner" },
  admin: { fa: "مدیر", en: "Administrator" },
  member: { fa: "عضو", en: "Member" },
  viewer: { fa: "بیننده", en: "Viewer" },
};

export function auditEventLabel(type: string, lang: Lang): string | null {
  return AUDIT_EVENT_LABELS[type]?.[lang] ?? null;
}

export function auditTargetLabel(type: string, lang: Lang): string {
  return TARGET_LABELS[type]?.[lang] ?? type;
}

export function auditDetailKeyLabel(key: string, lang: Lang): string {
  return DETAIL_KEY_LABELS[key]?.[lang] ?? key.replaceAll("_", " ");
}

/** A detail value in words where its meaning is known; otherwise as sent. */
export function auditDetailValue(key: string, value: string, lang: Lang): string {
  if (key === "previous_role" || key === "new_role" || key === "role") {
    return ROLE_VALUES[value]?.[lang] ?? value;
  }
  if (key === "fields") {
    return value
      .split(",")
      .map((field) => FIELD_LABELS[field.trim()]?.[lang] ?? field.trim())
      .join(lang === "fa" ? "، " : ", ");
  }
  return value;
}
