/**
 * Every string of the privacy page and the meeting's data panel (HOM-190),
 * in both console languages. The English strings are the contract the tests
 * look for; the Persian ones are written to read naturally.
 */
import type { ErrorCodeMessages } from "@/lib/error-messages";
import type { Lang } from "@/lib/i18n-context";
import type { LegalHoldFilter } from "@/lib/privacy-api";

const EN = {
    eyebrow: "Administration",
    heading: "Privacy",
    intro:
      "Export what the workspace holds about one person, and keep recordings from being deleted while a legal matter is open.",

    exportHeading: "Data subject export",
    exportIntro:
      "Find everything about one person by an e-mail, phone number, national ID, bank card or IBAN, and download it as a file.",
    identifierLabel: "Identifier",
    identifiersLabel: "Identifiers to export",
    add: "Add",
    removeIdentifier: "Remove",
    identifierTooLong: "An identifier can be at most 64 characters.",
    identifierLimit: "You can add at most 10 identifiers.",
    invalidIdentifier:
      "Each identifier must be exactly one e-mail, phone number, national ID, bank card or IBAN.",
    reasonLabel: "Reason",
    reasonHint: "3 to 200 characters. It is written to the audit log.",
    exportData: "Export data",
    exportWarningTitle: "This file contains personal data",
    exportWarningBody:
      "The file holds the original, unredacted transcript text about this person. Store it and send it only through your approved channel. This export is written to the audit log.",
    cancel: "Cancel",
    downloadExport: "Download export",
    exporting: "Preparing the export…",
    exportDone: "The export was downloaded.",
    exportFailed: "The export could not be created.",

    holdsHeading: "Legal holds",
    filterLabel: "Filter legal holds",
    filters: {
      all: "All holds",
      review_due: "Review due",
      due_30_days: "Due in 30 days",
    } satisfies Record<LegalHoldFilter, string>,
    emptyAll: "Nothing is on legal hold.",
    emptyReviewDue: "No hold is due for review.",
    emptyDue30: "No hold is due for review in the next 30 days.",
    holdsTable: "Recordings on legal hold",
    columns: {
      meeting: "Conversation",
      kind: "Recording",
      reason: "Reason",
      placedBy: "Placed by",
      placedAt: "Placed at",
      reviewAt: "Review at",
      actions: "Actions",
    },
    conversationDeleted: "Conversation deleted",
    untitledConversation: "Untitled conversation",
    unknownMember: "Unknown member",
    reviewDue: "Review due",
    release: "Release",
    loadMore: "Load more",
    loadMoreFailed: "More holds could not be loaded. Try again.",
    holdsFailed: "The legal holds could not be refreshed. The list may be out of date.",

    auditHeading: "Audit history",
    auditIntro: "Each link opens the audit log filtered to one kind of event.",
    auditExports: "Export history",
    auditPlaced: "Holds placed",
    auditReleased: "Holds released",

    panelHeading: "Data and retention",
    panelIntro: "The stored recordings of this conversation and how long they are kept.",
    mediaTable: "Stored recordings",
    mediaColumns: {
      kind: "Recording",
      state: "State",
      integrity: "Integrity",
      retention: "Kept until",
      hold: "Legal hold",
      actions: "Actions",
    },
    noRetention: "No end date",
    noMedia: "This conversation has no stored recordings.",
    mediaFailed: "The stored recordings could not be loaded.",
    reviewOn: "Review",
    placeHold: "Place legal hold",
    placeHoldHeading: "Place a legal hold",
    placeHoldBody:
      "Every live recording of this conversation is kept, even after its retention period or a deletion request, until the hold is released.",
    reviewDateLabel: "Review date",
    chooseReviewDate: "Choose a review date",
    reviewDateHint: "Between tomorrow and 10 years from today.",
    placeHoldConfirm: "Place hold",
    holdPlaced: "The legal hold was placed.",
    holdFailed: "The legal hold could not be placed.",

    releaseHeading: "Release the legal hold",
    releaseBody:
      "The recording can be deleted again by its retention period. A deletion that was requested while it was held runs now.",
    releaseConfirm: "Release hold",
    holdReleased: "The legal hold was released.",
    releaseFailed: "The legal hold could not be released.",

    kinds: {
      original: "Original recording",
      normalized: "Normalized audio",
      enhanced: "Enhanced audio",
      channel_derived: "Channel audio",
      clip: "Clip",
      export: "Export",
    } as Record<string, string>,
    states: {
      active: "Active",
      retention_pending: "Retention ending",
      deletion_pending: "Deletion pending",
      deleted: "Deleted",
      legal_hold: "On legal hold",
    } as Record<string, string>,
    integrities: {
      unverified: "Not checked",
      verified: "Verified",
      missing: "Missing",
      corrupt: "Damaged",
    } as Record<string, string>,
};

export type PrivacyCopy = typeof EN;

const FA: PrivacyCopy = {
    eyebrow: "مدیریت",
    heading: "حریم خصوصی",
    intro:
      "هر داده‌ای را که این فضای کاری دربارهٔ یک نفر دارد خروجی بگیرید، و تا وقتی یک موضوع حقوقی باز است جلوی حذف فایل‌های صوتی را بگیرید.",

    exportHeading: "خروجی داده‌های یک شخص",
    exportIntro:
      "همه چیز دربارهٔ یک نفر را با ایمیل، شمارهٔ تلفن، کد ملی، شمارهٔ کارت بانکی یا شبا پیدا کنید و به‌صورت فایل دریافت کنید.",
    identifierLabel: "شناسه",
    identifiersLabel: "شناسه‌های خروجی",
    add: "افزودن",
    removeIdentifier: "حذف",
    identifierTooLong: "هر شناسه حداکثر ۶۴ نویسه می‌تواند باشد.",
    identifierLimit: "حداکثر ۱۰ شناسه می‌توانید اضافه کنید.",
    invalidIdentifier:
      "هر شناسه باید دقیقاً یک ایمیل، شمارهٔ تلفن، کد ملی، شمارهٔ کارت بانکی یا شبا باشد.",
    reasonLabel: "دلیل",
    reasonHint: "بین ۳ تا ۲۰۰ نویسه. در گزارش رویدادها ثبت می‌شود.",
    exportData: "خروجی داده‌ها",
    exportWarningTitle: "این فایل داده‌های شخصی دارد",
    exportWarningBody:
      "این فایل متن اصلی و پنهان‌نشدهٔ گفت‌وگوها دربارهٔ این شخص را دارد. آن را فقط از راه کانال تأییدشدهٔ سازمان نگه دارید و بفرستید. این خروجی در گزارش رویدادها ثبت می‌شود.",
    cancel: "انصراف",
    downloadExport: "دریافت فایل خروجی",
    exporting: "در حال آماده‌سازی خروجی…",
    exportDone: "فایل خروجی دریافت شد.",
    exportFailed: "خروجی ساخته نشد.",

    holdsHeading: "توقیف‌های قانونی",
    filterLabel: "فیلتر توقیف‌های قانونی",
    filters: {
      all: "همهٔ توقیف‌ها",
      review_due: "موعد بازبینی رسیده",
      due_30_days: "موعد تا ۳۰ روز",
    } satisfies Record<LegalHoldFilter, string>,
    emptyAll: "هیچ موردی در توقیف قانونی نیست.",
    emptyReviewDue: "موعد بازبینی هیچ توقیفی نرسیده است.",
    emptyDue30: "موعد بازبینی هیچ توقیفی در ۳۰ روز آینده نیست.",
    holdsTable: "فایل‌های صوتی در توقیف قانونی",
    columns: {
      meeting: "گفت‌وگو",
      kind: "فایل صوتی",
      reason: "دلیل",
      placedBy: "ثبت‌کننده",
      placedAt: "زمان ثبت",
      reviewAt: "زمان بازبینی",
      actions: "عملیات",
    },
    conversationDeleted: "گفت‌وگو حذف شده است",
    untitledConversation: "گفت‌وگوی بی‌عنوان",
    unknownMember: "عضو نامشخص",
    reviewDue: "موعد بازبینی",
    release: "رفع توقیف",
    loadMore: "نمایش بیشتر",
    loadMoreFailed: "موارد بیشتر دریافت نشد. دوباره تلاش کنید.",
    holdsFailed: "فهرست توقیف‌های قانونی به‌روز نشد. ممکن است فهرست قدیمی باشد.",

    auditHeading: "سابقه در گزارش رویدادها",
    auditIntro: "هر پیوند، گزارش رویدادها را برای یک نوع رویداد باز می‌کند.",
    auditExports: "سابقهٔ خروجی‌ها",
    auditPlaced: "توقیف‌های ثبت‌شده",
    auditReleased: "توقیف‌های رفع‌شده",

    panelHeading: "داده‌ها و نگهداری",
    panelIntro: "فایل‌های صوتی ذخیره‌شدهٔ این گفت‌وگو و مدت نگهداری آن‌ها.",
    mediaTable: "فایل‌های صوتی ذخیره‌شده",
    mediaColumns: {
      kind: "فایل صوتی",
      state: "وضعیت",
      integrity: "سلامت فایل",
      retention: "نگهداری تا",
      hold: "توقیف قانونی",
      actions: "عملیات",
    },
    noRetention: "بدون تاریخ پایان",
    noMedia: "این گفت‌وگو فایل صوتی ذخیره‌شده ندارد.",
    mediaFailed: "فایل‌های صوتی ذخیره‌شده دریافت نشد.",
    reviewOn: "بازبینی",
    placeHold: "ثبت توقیف قانونی",
    placeHoldHeading: "ثبت توقیف قانونی",
    placeHoldBody:
      "همهٔ فایل‌های صوتی موجود این گفت‌وگو، حتی پس از پایان مدت نگهداری یا درخواست حذف، تا رفع توقیف نگه داشته می‌شوند.",
    reviewDateLabel: "تاریخ بازبینی",
    chooseReviewDate: "انتخاب تاریخ بازبینی",
    reviewDateHint: "از فردا تا ۱۰ سال بعد.",
    placeHoldConfirm: "ثبت توقیف",
    holdPlaced: "توقیف قانونی ثبت شد.",
    holdFailed: "توقیف قانونی ثبت نشد.",

    releaseHeading: "رفع توقیف قانونی",
    releaseBody:
      "پس از رفع توقیف، فایل دوباره طبق مدت نگهداری حذف می‌شود. اگر در زمان توقیف درخواست حذف ثبت شده باشد، همین حالا اجرا می‌شود.",
    releaseConfirm: "رفع توقیف",
    holdReleased: "توقیف قانونی رفع شد.",
    releaseFailed: "توقیف قانونی رفع نشد.",

    kinds: {
      original: "فایل اصلی",
      normalized: "صدای یکسان‌سازی‌شده",
      enhanced: "صدای بهبودیافته",
      channel_derived: "صدای یک کانال",
      clip: "بخش کوتاه",
      export: "خروجی",
    } as Record<string, string>,
    states: {
      active: "فعال",
      retention_pending: "پایان مدت نگهداری",
      deletion_pending: "در انتظار حذف",
      deleted: "حذف‌شده",
      legal_hold: "در توقیف قانونی",
    } as Record<string, string>,
    integrities: {
      unverified: "بررسی‌نشده",
      verified: "تأییدشده",
      missing: "پیدا نشد",
      corrupt: "آسیب‌دیده",
    } as Record<string, string>,
};

export const PRIVACY_COPY: Record<Lang, PrivacyCopy> = { en: EN, fa: FA };

/** A readable label from a copy table; the raw value when the server sends a new one. */
export function labelOf(table: Record<string, string>, value: string): string {
  return Object.hasOwn(table, value) ? table[value] : value;
}

/** Texts for the hold and release errors (SPEC section 4). */
export const HOLD_ERROR_CODES: ErrorCodeMessages = {
  not_held: {
    en: "This recording is not on legal hold. It may have been released already.",
    fa: "این فایل صوتی در توقیف قانونی نیست. شاید قبلاً رفع توقیف شده باشد.",
  },
  already_deleted: {
    en: "This recording was already deleted and cannot be held.",
    fa: "این فایل صوتی قبلاً حذف شده است و نمی‌توان آن را توقیف کرد.",
  },
  hold_review_invalid: {
    en: "Choose a review date in the future, at most 10 years from today.",
    fa: "تاریخ بازبینی را در آینده و حداکثر ۱۰ سال بعد از امروز انتخاب کنید.",
  },
  media_not_found: {
    en: "This recording no longer exists.",
    fa: "این فایل صوتی دیگر وجود ندارد.",
  },
  permission_denied: {
    en: "Only the workspace owner can do this.",
    fa: "فقط مالک فضای کاری می‌تواند این کار را انجام دهد.",
  },
};
