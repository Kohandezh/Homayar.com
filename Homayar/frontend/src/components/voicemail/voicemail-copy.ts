import type {
  FollowUpState,
  VoicemailCategory,
  VoicemailPriority,
  VoicemailStatus,
} from "@/types/voicemail";

/**
 * Every voicemail page text, in the console's two languages (HOM-252). An
 * enum value the tables do not know is shown raw, never guessed.
 */
export const VOICEMAIL_COPY = {
  en: {
    eyebrow: "Workspace",
    title: "Voicemail",
    recordScope: "You see only the voicemails your record scope allows.",

    loading: "Loading voicemails",
    loadFailed: "Could not load the voicemail list.",
    retry: "Try again",
    noneYet: "No voicemails yet",
    noMatch: "No voicemail matches these filters",
    noMore: "No more voicemails.",

    filterPriority: "Priority",
    filterCategory: "Category",
    all: "All",

    tableLabel: "Voicemails",
    columnReceived: "Received",
    columnCaller: "Caller",
    columnCategory: "Category",
    columnPriority: "Priority",
    columnStatus: "Status",
    columnFollowUp: "Follow-up",
    columnDuration: "Duration",
    columnActions: "Actions",

    unknownCaller: "Unknown caller",
    notClassified: "Not classified",
    details: "Details",
    detailsFor: (caller: string, date: string) => `Details for ${caller}, ${date}`,

    previous: "Previous",
    next: "Next",
    page: (number: string) => `Page ${number}`,
    pagingLabel: "Voicemail pagination",

    drawerLabel: "Voicemail details",
    closeDetails: "Close details",
    fieldMailbox: "Mailbox",
    fieldExtension: "Extension",
    fieldDuration: "Duration",
    fieldStatus: "Status",
    fieldCategory: "Category",
    fieldIntent: "Intent",
    fieldTopics: "Topics",
    fieldPriority: "Priority",
    fieldPriorityScore: "Priority score",
    fieldConfidence: "Classification confidence",
    fieldClassifier: "Classifier",
    fieldMeetingStatus: "Meeting status",
    notRecorded: "Not recorded",
    noTopics: "—",

    transcriptTitle: "Transcript",
    noTranscript: "No transcript yet",

    followUpTitle: "Follow-up",
    followUpHandledAt: "Handled at",
    followUpAction: "Follow-up action",
    actionNotAvailable: "The follow-up action is not available",
    openCall: "Open the call",

    markHandled: "Mark handled",
    reopen: "Reopen",
    forbidden: "You cannot change voicemails in this workspace",
    notFound: "This voicemail was not found, or it is outside your record scope.",
    updateFailed: "The change could not be saved; try again.",

    cardTitle: "Voicemail",
    cardEmpty: "No voicemails in the last 30 days",
    cardTotal: "Total",
    cardHandled: "Handled",
    cardOpen: "Open",
    cardUrgent: "Urgent",
    cardMedian: "Median follow-up time",
    cardOpenInbox: "Open the voicemail inbox",

    priorities: {
      urgent: "Urgent",
      high: "High",
      normal: "Normal",
      low: "Low",
    },
    categories: {
      complaint: "Complaint",
      sales_inquiry: "Sales inquiry",
      support: "Support",
      appointment: "Appointment",
      callback_request: "Callback request",
      billing: "Billing",
      spam: "Spam",
      empty: "Empty",
      other: "Other",
    },
    statuses: {
      received: "Received",
      processing: "Processing",
      transcribed: "Transcribed",
      classified: "Classified",
      needs_review: "Needs review",
      failed: "Failed",
    },
    followUpStates: {
      none: "No follow-up",
      pending: "Pending",
      action_created: "Action created",
      done: "Handled",
    },
  },
  fa: {
    eyebrow: "فضای کاری",
    title: "پیام‌های صوتی",
    recordScope: "فقط پیام‌های صوتی در محدودهٔ دسترسی رکورد شما نمایش داده می‌شود.",

    loading: "در حال دریافت پیام‌های صوتی",
    loadFailed: "دریافت فهرست پیام‌های صوتی ناموفق بود.",
    retry: "تلاش دوباره",
    noneYet: "هنوز پیام صوتی‌ای نیست",
    noMatch: "هیچ پیام صوتی‌ای با این فیلترها پیدا نشد",
    noMore: "پیام صوتی دیگری نیست.",

    filterPriority: "اولویت",
    filterCategory: "دسته",
    all: "همه",

    tableLabel: "پیام‌های صوتی",
    columnReceived: "دریافت‌شده",
    columnCaller: "تماس‌گیرنده",
    columnCategory: "دسته",
    columnPriority: "اولویت",
    columnStatus: "وضعیت",
    columnFollowUp: "پیگیری",
    columnDuration: "مدت",
    columnActions: "عملیات",

    unknownCaller: "تماس‌گیرندهٔ ناشناس",
    notClassified: "طبقه‌بندی نشده",
    details: "جزئیات",
    detailsFor: (caller: string, date: string) => `جزئیات ${caller}، ${date}`,

    previous: "قبلی",
    next: "بعدی",
    page: (number: string) => `صفحه ${number}`,
    pagingLabel: "صفحه‌بندی پیام‌های صوتی",

    drawerLabel: "جزئیات پیام صوتی",
    closeDetails: "بستن جزئیات",
    fieldMailbox: "صندوق پستی",
    fieldExtension: "داخلی",
    fieldDuration: "مدت",
    fieldStatus: "وضعیت",
    fieldCategory: "دسته",
    fieldIntent: "قصد",
    fieldTopics: "موضوع‌ها",
    fieldPriority: "اولویت",
    fieldPriorityScore: "امتیاز اولویت",
    fieldConfidence: "اطمینان طبقه‌بندی",
    fieldClassifier: "طبقه‌بند",
    fieldMeetingStatus: "وضعیت جلسه",
    notRecorded: "ثبت نشده",
    noTopics: "—",

    transcriptTitle: "رونوشت",
    noTranscript: "هنوز رونوشتی نیست",

    followUpTitle: "پیگیری",
    followUpHandledAt: "زمان رسیدگی",
    followUpAction: "اقدام پیگیری",
    actionNotAvailable: "اقدام پیگیری در دسترس نیست",
    openCall: "باز کردن تماس",

    markHandled: "علامت‌گذاری به‌عنوان رسیدگی‌شده",
    reopen: "بازگشایی",
    forbidden: "شما نمی‌توانید پیام‌های صوتی این فضای کاری را تغییر دهید",
    notFound: "این پیام صوتی پیدا نشد، یا بیرون از محدودهٔ دسترسی رکورد شماست.",
    updateFailed: "تغییر ذخیره نشد؛ دوباره تلاش کنید.",

    cardTitle: "پیام‌های صوتی",
    cardEmpty: "در ۳۰ روز گذشته پیام صوتی‌ای نیست",
    cardTotal: "مجموع",
    cardHandled: "رسیدگی‌شده",
    cardOpen: "باز",
    cardUrgent: "فوری",
    cardMedian: "میانهٔ زمان رسیدگی",
    cardOpenInbox: "باز کردن صندوق پیام‌های صوتی",

    priorities: {
      urgent: "فوری",
      high: "زیاد",
      normal: "عادی",
      low: "کم",
    },
    categories: {
      complaint: "شکایت",
      sales_inquiry: "درخواست خرید",
      support: "پشتیبانی",
      appointment: "نوبت",
      callback_request: "درخواست تماس",
      billing: "صورت‌حساب",
      spam: "هرزنامه",
      empty: "خالی",
      other: "سایر",
    },
    statuses: {
      received: "دریافت‌شده",
      processing: "در حال پردازش",
      transcribed: "رونویسی‌شده",
      classified: "طبقه‌بندی‌شده",
      needs_review: "نیازمند بررسی",
      failed: "ناموفق",
    },
    followUpStates: {
      none: "بدون پیگیری",
      pending: "در انتظار",
      action_created: "اقدام ثبت شد",
      done: "رسیدگی‌شده",
    },
  },
} as const;

export type VoicemailCopy = (typeof VOICEMAIL_COPY)["en" | "fa"];

type Lang = "fa" | "en";

/**
 * `urgent` -> "Urgent" / "فوری". The types list every value the API sends, but
 * a newer backend can add one; an unknown value is shown as sent, never
 * guessed, and never read off the prototype (`"constructor"` is not a label).
 */
export function voicemailPriorityLabel(priority: VoicemailPriority, lang: Lang) {
  const labels = VOICEMAIL_COPY[lang].priorities;
  return Object.hasOwn(labels, priority) ? labels[priority as keyof typeof labels] : priority;
}

export function voicemailCategoryLabel(category: VoicemailCategory, lang: Lang) {
  const labels = VOICEMAIL_COPY[lang].categories;
  return Object.hasOwn(labels, category) ? labels[category as keyof typeof labels] : category;
}

export function voicemailStatusLabel(status: VoicemailStatus, lang: Lang) {
  const labels = VOICEMAIL_COPY[lang].statuses;
  return Object.hasOwn(labels, status) ? labels[status as keyof typeof labels] : status;
}

export function followUpStateLabel(state: FollowUpState, lang: Lang) {
  const labels = VOICEMAIL_COPY[lang].followUpStates;
  return Object.hasOwn(labels, state) ? labels[state as keyof typeof labels] : state;
}

/**
 * The Chip look of a priority: urgent stands out in danger and high warns, both
 * soft; normal is the default chip (not soft), low stays quiet in default soft.
 */
export function voicemailPriorityChip(priority: VoicemailPriority) {
  if (priority === "urgent") return { color: "danger" as const, variant: "soft" as const };
  if (priority === "high") return { color: "warning" as const, variant: "soft" as const };
  if (priority === "normal") return { color: "default" as const, variant: "secondary" as const };
  return { color: "default" as const, variant: "soft" as const };
}
