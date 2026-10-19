export type ResultsTab = "transcript" | "actions" | "shares" | "export";

export interface ResultsCopy {
  tabs: Record<ResultsTab, string>;
  tabList: string;
  exportFormat: string;
  exportAction: string;
  loading: string;
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
  correction: {
    editTurn: (time: string) => string;
    turnText: string;
    speaker: string;
    reasonLabel: string;
    reasonHint: string;
    save: string;
    savedAsRevision: (revision: string) => string;
    reloadTranscript: string;
    errors: Record<string, string>;
    failed: string;
  };
  revisions: {
    title: string;
    current: string;
    close: string;
    label: (revision: string) => string;
    sources: Record<"asr" | "correction" | "backfill", string>;
    you: string;
    system: string;
    unknownMember: string;
    view: (revision: string) => string;
    backToCurrent: string;
    viewingRevision: (revision: string) => string;
    empty: string;
    failed: string;
  };
  contentView: {
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
    tabs: { transcript: "Transcript", actions: "Action items", shares: "Share links", export: "Export" },
    tabList: "Meeting results",
    exportFormat: "Export format",
    exportAction: "Download export",
    loading: "Loading",
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
    correction: {
      editTurn: (time) => `Edit turn at ${time}`,
      turnText: "Turn text",
      speaker: "Speaker",
      reasonLabel: "Reason for the correction",
      reasonHint: "Required. 3 to 500 characters.",
      save: "Save correction",
      savedAsRevision: (revision) => `Saved as revision ${revision}.`,
      reloadTranscript: "Reload transcript",
      errors: {
        revision_stale: "The transcript changed since it was loaded. Reload it and make the correction again.",
        reason_required: "Enter a reason.",
        no_changes: "The correction changes nothing; edit the text or the speaker.",
        invalid_text: "The text is empty or longer than 8000 characters.",
        invalid_speaker: "That speaker is not known for this meeting.",
        invalid_segment: "This segment is not in the loaded transcript; reload the transcript.",
        duplicate_edit: "The same segment was sent twice; reload the transcript and try again.",
        permission_denied: "Your role cannot correct the transcript.",
      },
      failed: "The correction could not be saved.",
    },
    revisions: {
      title: "Revision history",
      current: "Current",
      close: "Close revision history",
      label: (revision) => `Revision ${revision}`,
      sources: {
        asr: "Automatic transcription",
        correction: "Correction",
        backfill: "Backfill",
      },
      you: "You",
      system: "System",
      unknownMember: "Unknown member",
      view: (revision) => `View revision ${revision}`,
      backToCurrent: "Back to current",
      viewingRevision: (revision) => `You are viewing revision ${revision}. It is read only.`,
      empty: "No transcript revisions yet.",
      failed: "The revisions could not be loaded.",
    },
    contentView: {
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
    tabs: { transcript: "متن گفت‌وگو", actions: "اقدام‌ها", shares: "لینک‌های اشتراک", export: "خروجی" },
    tabList: "نتایج جلسه",
    exportFormat: "قالب خروجی",
    exportAction: "دریافت فایل خروجی",
    loading: "در حال بارگذاری",
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
    correction: {
      editTurn: (time) => `ویرایش بخش در ${time}`,
      turnText: "متن بخش",
      speaker: "گوینده",
      reasonLabel: "دلیل اصلاح",
      reasonHint: "الزامی. ۳ تا ۵۰۰ نویسه.",
      save: "ذخیرهٔ اصلاح",
      savedAsRevision: (revision) => `به‌عنوان نسخهٔ ${revision} ذخیره شد.`,
      reloadTranscript: "بارگذاری دوبارهٔ متن",
      errors: {
        revision_stale: "متن از زمان بارگذاری تغییر کرده است. آن را دوباره بارگذاری کنید و اصلاح را دوباره انجام دهید.",
        reason_required: "دلیلی وارد کنید.",
        no_changes: "این اصلاح چیزی را تغییر نمی‌دهد؛ متن یا گوینده را تغییر دهید.",
        invalid_text: "متن خالی است یا از ۸۰۰۰ نویسه بلندتر است.",
        invalid_speaker: "این گوینده برای این جلسه شناخته نشده است.",
        invalid_segment: "این بخش در متن بارگذاری‌شده نیست؛ متن را دوباره بارگذاری کنید.",
        duplicate_edit: "یک بخش دو بار ارسال شده است؛ متن را دوباره بارگذاری کنید و دوباره تلاش کنید.",
        permission_denied: "نقش شما اجازهٔ اصلاح متن را ندارد.",
      },
      failed: "اصلاح ذخیره نشد.",
    },
    revisions: {
      title: "تاریخچهٔ نسخه‌ها",
      current: "فعلی",
      close: "بستن تاریخچهٔ نسخه‌ها",
      label: (revision) => `نسخهٔ ${revision}`,
      sources: {
        asr: "تبدیل گفتار به متن خودکار",
        correction: "اصلاح",
        backfill: "بازسازی سیستمی",
      },
      you: "شما",
      system: "سیستم",
      unknownMember: "عضو ناشناس",
      view: (revision) => `مشاهدهٔ نسخهٔ ${revision}`,
      backToCurrent: "بازگشت به نسخهٔ فعلی",
      viewingRevision: (revision) => `در حال مشاهدهٔ نسخهٔ ${revision} هستید. فقط خواندنی است.`,
      empty: "هنوز نسخه‌ای ثبت نشده است.",
      failed: "نسخه‌ها دریافت نشد.",
    },
    contentView: {
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
