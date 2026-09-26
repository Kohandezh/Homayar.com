export type ResultsTab = "summary" | "transcript" | "actions" | "shares";

export interface ResultsCopy {
  tabs: Record<ResultsTab, string>;
  tabList: string;
  exportFormat: string;
  exportAction: string;
  loading: string;
  summary: {
    overview: string;
    decisions: string;
    keyPoints: string;
    topics: string;
    evidence: string;
    showSource: string;
    earlierRevision: string;
    notReady: string;
    unavailableBecause: (reason: string) => string;
    failed: string;
    mock: string;
  };
  transcript: {
    empty: string;
    failed: string;
    speakers: string;
    allSpeakers: string;
    speakerFilter: string;
    renameSpeaker: (name: string) => string;
    saveSpeaker: string;
    privacyTitle: string;
    masked: (count: string, classes: string) => string;
    maskedLimits: string;
    showOriginal: string;
    confirmOriginal: string;
    confirmOriginalAction: string;
    cancel: string;
    viewingOriginal: string;
    hideOriginal: string;
    originalDenied: string;
    originalFailed: string;
    placeholder: (label: string) => string;
    revealTitle: (placeholder: string) => string;
    revealReason: string;
    revealSubmit: string;
    revealed: string;
    hideValue: string;
    revealErrors: Record<string, string>;
    revealFailed: string;
    classes: Record<string, string>;
    atFound: (time: string) => string;
    atMissing: string;
  };
  contentView: {
    summaryRedacted: string;
    summaryOriginal: string;
    analysisOnRedacted: string;
    actionsRedacted: string;
    actionsOriginal: string;
  };
  actions: {
    description: string;
    owner: string;
    priority: string;
    add: string;
    empty: string;
    failed: string;
    status: string;
    delete: string;
    showSource: string;
  };
  shares: {
    expires: string;
    hours: Record<number, string>;
    requireSignIn: string;
    create: string;
    copy: string;
    empty: string;
    failed: string;
    readOnly: string;
    signInRequired: string;
    public: string;
    noExpiry: string;
    accessCount: (count: string) => string;
    revoke: string;
    revokeNote: string;
  };
  quality: {
    title: string;
    labels: Record<"transcription" | "speaker_attribution" | "analysis" | "evidence", string>;
    stale: string;
    coverage: (percent: string) => string;
    mock: string;
  };
  operationFailed: string;
}

export const RESULTS_COPY: Record<"fa" | "en", ResultsCopy> = {
  en: {
    tabs: { summary: "Summary", transcript: "Transcript", actions: "Action items", shares: "Share links" },
    tabList: "Meeting results",
    exportFormat: "Export format",
    exportAction: "Export",
    loading: "Loading",
    summary: {
      overview: "Summary",
      decisions: "Decisions",
      keyPoints: "Key points",
      topics: "Topics",
      evidence: "Evidence",
      showSource: "Show in transcript",
      earlierRevision: "cites an earlier transcript revision",
      notReady: "No summary is available for this meeting.",
      unavailableBecause: (reason) => `The analysis is not available: ${reason}`,
      failed: "The summary could not be loaded.",
      mock: "Produced by a test (mock) provider — not a real analysis.",
    },
    transcript: {
      empty: "The transcript is empty.",
      failed: "The transcript could not be loaded.",
      speakers: "Speakers",
      allSpeakers: "All speakers",
      speakerFilter: "Show speaker",
      renameSpeaker: (name) => `Rename ${name}`,
      saveSpeaker: "Save speaker name",
      privacyTitle: "Personal data masking",
      masked: (count, classes) =>
        `This workspace masks personal data. In this transcript, ${count} item(s) are masked (${classes}).`,
      maskedLimits:
        "Only structured data is detected (emails, phone numbers, national ID, card and IBAN numbers, postal codes); names and addresses are not. The audio recording is not masked.",
      showOriginal: "Show original transcript",
      confirmOriginal:
        "Viewing the original is recorded in the workspace audit log under your name. It is shown only on this page and cleared when you hide it or leave.",
      confirmOriginalAction: "Show original",
      cancel: "Cancel",
      viewingOriginal: "You are viewing the original transcript. This access was recorded in the audit log.",
      hideOriginal: "Hide original",
      originalDenied: "Your role cannot view the original transcript in this workspace.",
      originalFailed: "The original transcript could not be loaded.",
      placeholder: (label) => `Masked ${label}`,
      revealTitle: (placeholder) => `Reveal ${placeholder}`,
      revealReason: "Reason for revealing this value (recorded in the audit log)",
      revealSubmit: "Reveal",
      revealed: "Revealed value (recorded in the audit log)",
      hideValue: "Hide value",
      revealErrors: {
        mapping_not_retained: "The original of this value was not kept, so it cannot be revealed.",
        mapping_key_unavailable: "The key needed to reveal this value is not available on the server.",
        original_access_denied: "Your role cannot reveal masked values.",
        permission_denied: "Your role cannot reveal masked values.",
        redaction_not_found: "This masked version no longer exists; reload the transcript.",
        validation_error: "Enter a reason of 3 to 200 characters.",
      },
      revealFailed: "The value could not be revealed.",
      classes: {
        email: "email",
        phone: "phone number",
        ir_national_id: "national ID",
        bank_card: "card number",
        iban: "IBAN",
        ir_postal_code: "postal code",
      },
      atFound: (time) => `Showing the transcript segment at ${time}.`,
      atMissing: "No transcript segment was found at the requested time.",
    },
    contentView: {
      summaryRedacted: "This summary is shown with personal data masked by the same detector as the transcript (structured data only).",
      summaryOriginal: "This summary is shown without masking.",
      analysisOnRedacted: "The analysis was produced from the masked transcript.",
      actionsRedacted: "These action items are shown with personal data masked by the same detector as the transcript (structured data only).",
      actionsOriginal: "These action items are shown without masking.",
    },
    actions: {
      description: "Action description",
      owner: "Owner",
      priority: "Priority",
      add: "Add",
      empty: "No action items",
      failed: "Action items could not be loaded.",
      status: "Action status",
      delete: "Delete action",
      showSource: "Show source in transcript",
    },
    shares: {
      expires: "Expires after",
      hours: { 24: "24 hours", 168: "7 days", 720: "30 days" },
      requireSignIn: "Require sign-in",
      create: "Create link",
      copy: "Copy share link",
      empty: "No share links",
      failed: "Share links could not be loaded.",
      readOnly: "Your role in this workspace is read-only.",
      signInRequired: "Sign-in required",
      public: "Anyone with the link",
      noExpiry: "No expiry",
      accessCount: (count) => `${count} views`,
      revoke: "Revoke share link",
      revokeNote: "Revoking a link stops access through it; copies people already downloaded are not affected.",
    },
    quality: {
      title: "Quality and coverage",
      labels: {
        transcription: "Transcription",
        speaker_attribution: "Speaker attribution",
        analysis: "Analysis",
        evidence: "Evidence",
      },
      stale: "The transcript was corrected after the analysis; the summary and actions cite the earlier revision.",
      coverage: (percent) => `Speech coverage: ${percent}`,
      mock: "Contains test (mock) provider output",
    },
    operationFailed: "The requested operation failed.",
  },
  fa: {
    tabs: { summary: "خلاصه", transcript: "متن گفت‌وگو", actions: "اقدام‌ها", shares: "لینک‌های اشتراک" },
    tabList: "نتایج جلسه",
    exportFormat: "قالب خروجی",
    exportAction: "دریافت خروجی",
    loading: "در حال بارگذاری",
    summary: {
      overview: "خلاصه",
      decisions: "تصمیم‌ها",
      keyPoints: "نکات کلیدی",
      topics: "موضوع‌ها",
      evidence: "شواهد",
      showSource: "نمایش در متن",
      earlierRevision: "به نسخهٔ قبلی متن اشاره دارد",
      notReady: "برای این جلسه خلاصه‌ای در دسترس نیست.",
      unavailableBecause: (reason) => `تحلیل در دسترس نیست: ${reason}`,
      failed: "خلاصه دریافت نشد.",
      mock: "توسط ارائه‌دهندهٔ آزمایشی (mock) تولید شده است — تحلیل واقعی نیست.",
    },
    transcript: {
      empty: "متن گفت‌وگو خالی است.",
      failed: "متن گفت‌وگو دریافت نشد.",
      speakers: "گوینده‌ها",
      allSpeakers: "همهٔ گوینده‌ها",
      speakerFilter: "نمایش گوینده",
      renameSpeaker: (name) => `تغییر نام ${name}`,
      saveSpeaker: "ذخیرهٔ نام گوینده",
      privacyTitle: "پوشاندن داده‌های شخصی",
      masked: (count, classes) =>
        `این فضای کاری داده‌های شخصی را می‌پوشاند. در این متن ${count} مورد پوشانده شده است (${classes}).`,
      maskedLimits:
        "فقط داده‌های ساخت‌یافته تشخیص داده می‌شوند (ایمیل، شمارهٔ تلفن، کد ملی، شمارهٔ کارت، شبا، کد پستی)؛ نام‌ها و نشانی‌ها تشخیص داده نمی‌شوند. فایل صوتی پوشانده نمی‌شود.",
      showOriginal: "نمایش متن اصلی",
      confirmOriginal:
        "مشاهدهٔ متن اصلی با نام شما در گزارش ممیزی فضای کاری ثبت می‌شود. متن فقط در همین صفحه نمایش داده می‌شود و با پنهان کردن یا ترک صفحه پاک می‌شود.",
      confirmOriginalAction: "نمایش متن اصلی",
      cancel: "انصراف",
      viewingOriginal: "در حال مشاهدهٔ متن اصلی هستید. این دسترسی در گزارش ممیزی ثبت شد.",
      hideOriginal: "پنهان کردن متن اصلی",
      originalDenied: "نقش شما در این فضای کاری اجازهٔ مشاهدهٔ متن اصلی را ندارد.",
      originalFailed: "متن اصلی دریافت نشد.",
      placeholder: (label) => `${label} پوشانده‌شده`,
      revealTitle: (placeholder) => `نمایش ${placeholder}`,
      revealReason: "دلیل نمایش این مقدار (در گزارش ممیزی ثبت می‌شود)",
      revealSubmit: "نمایش",
      revealed: "مقدار نمایش‌داده‌شده (در گزارش ممیزی ثبت شد)",
      hideValue: "پنهان کردن مقدار",
      revealErrors: {
        mapping_not_retained: "نسخهٔ اصلی این مقدار نگهداری نشده است و قابل نمایش نیست.",
        mapping_key_unavailable: "کلید لازم برای نمایش این مقدار روی سرور در دسترس نیست.",
        original_access_denied: "نقش شما اجازهٔ نمایش مقدارهای پوشانده‌شده را ندارد.",
        permission_denied: "نقش شما اجازهٔ نمایش مقدارهای پوشانده‌شده را ندارد.",
        redaction_not_found: "این نسخهٔ پوشانده‌شده دیگر وجود ندارد؛ متن را دوباره بارگذاری کنید.",
        validation_error: "دلیلی بین ۳ تا ۲۰۰ نویسه وارد کنید.",
      },
      revealFailed: "مقدار نمایش داده نشد.",
      classes: {
        email: "ایمیل",
        phone: "شمارهٔ تلفن",
        ir_national_id: "کد ملی",
        bank_card: "شمارهٔ کارت",
        iban: "شبا",
        ir_postal_code: "کد پستی",
      },
      atFound: (time) => `بخش متن در زمان ${time} نمایش داده شده است.`,
      atMissing: "در زمان درخواستی بخشی از متن پیدا نشد.",
    },
    contentView: {
      summaryRedacted: "این خلاصه با پوشاندن داده‌های شخصی توسط همان تشخیص‌دهندهٔ متن نمایش داده می‌شود (فقط داده‌های ساخت‌یافته).",
      summaryOriginal: "این خلاصه بدون پوشش نمایش داده می‌شود.",
      analysisOnRedacted: "تحلیل روی متن پوشانده‌شده انجام شده است.",
      actionsRedacted: "این اقدام‌ها با پوشاندن داده‌های شخصی توسط همان تشخیص‌دهندهٔ متن نمایش داده می‌شوند (فقط داده‌های ساخت‌یافته).",
      actionsOriginal: "این اقدام‌ها بدون پوشش نمایش داده می‌شوند.",
    },
    actions: {
      description: "شرح اقدام",
      owner: "مسئول",
      priority: "اولویت",
      add: "افزودن",
      empty: "اقدامی ثبت نشده است",
      failed: "اقدام‌ها دریافت نشد.",
      status: "وضعیت اقدام",
      delete: "حذف اقدام",
      showSource: "نمایش منبع در متن",
    },
    shares: {
      expires: "اعتبار",
      hours: { 24: "۲۴ ساعت", 168: "۷ روز", 720: "۳۰ روز" },
      requireSignIn: "نیازمند ورود",
      create: "ساخت لینک",
      copy: "رونوشت لینک اشتراک",
      empty: "لینک اشتراکی وجود ندارد",
      failed: "لینک‌های اشتراک دریافت نشد.",
      readOnly: "نقش شما در این فضای کاری فقط خواندنی است.",
      signInRequired: "ورود الزامی",
      public: "هر کس لینک را داشته باشد",
      noExpiry: "بدون انقضا",
      accessCount: (count) => `${count} بازدید`,
      revoke: "لغو لینک اشتراک",
      revokeNote: "لغو لینک، دسترسی از طریق آن را قطع می‌کند؛ نسخه‌هایی که پیش‌تر دریافت شده‌اند تحت تأثیر قرار نمی‌گیرند.",
    },
    quality: {
      title: "کیفیت و پوشش",
      labels: {
        transcription: "متن گفتار",
        speaker_attribution: "انتساب گوینده",
        analysis: "تحلیل",
        evidence: "شواهد",
      },
      stale: "متن پس از تحلیل اصلاح شده است؛ خلاصه و اقدام‌ها به نسخهٔ قبلی متن اشاره می‌کنند.",
      coverage: (percent) => `پوشش گفتار: ${percent}`,
      mock: "شامل خروجی ارائه‌دهندهٔ آزمایشی (mock)",
    },
    operationFailed: "عملیات درخواست‌شده انجام نشد.",
  },
};

/** Placeholders the backend writes for masked values, e.g. [EMAIL_1] (PiiRevealRequest pattern). */
export const PLACEHOLDER_PATTERN = /\[[A-Z_]{3,24}_[0-9]{1,5}\]/g;
