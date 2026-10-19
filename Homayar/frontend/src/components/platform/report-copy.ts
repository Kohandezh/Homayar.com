import type { ReportFinding, ReportOutcomeState, ReviewActionKey } from "@/types/platform";

export interface ReportCopy {
  loading: string;
  failed: string;
  headings: {
    completeness: string;
    summary: string;
    outcome: string;
    qa: string;
    reviewActions: string;
  };
  /** One label per notice code of `completeness.notices`; an unknown code is shown as sent. */
  notices: Record<string, string>;
  summary: {
    noSummary: string;
    unavailableBecause: (reason: string) => string;
    notSupported: string;
    topics: string;
    sentiment: string;
    evidence: string;
    redacted: string;
    original: string;
    analysisOnRedacted: string;
  };
  outcome: {
    states: Record<ReportOutcomeState, string>;
    noEvidence: string;
  };
  qa: {
    counts: (supported: string, total: string) => string;
    none: string;
    types: Record<ReportFinding["finding_type"], string>;
  };
  evidence: {
    play: (time: string) => string;
    showInTranscript: string;
    earlierRevision: string;
    speaker: string;
  };
  reviewActions: {
    labels: Record<ReviewActionKey, string>;
    /** Why an action is not available; an unknown reason is shown as sent. */
    reasons: Record<string, string>;
    /** An available action whose panel exists only once the meeting has results. */
    needsResults: string;
  };
  advanced: string;
  /** On an item whose analysis read an earlier transcript revision (HOM-246). */
  staleMarker: string;
}

export const REPORT_COPY: Record<"fa" | "en", ReportCopy> = {
  en: {
    loading: "Loading the call report",
    failed: "The call report could not be loaded.",
    headings: {
      completeness: "Report completeness",
      summary: "Summary",
      outcome: "Outcome",
      qa: "QA findings",
      reviewActions: "Review actions",
    },
    notices: {
      processing_in_progress: "Processing is still running. This report is not final.",
      processing_failed: "Processing failed. This report is incomplete.",
      transcript_unavailable: "No transcript is available.",
      transcript_partial: "The transcript is partial.",
      analysis_unavailable: "No analysis is available.",
      analysis_partial: "The analysis is partial.",
      analysis_stale: "The transcript changed after the analysis. Results may be out of date.",
      mock_analysis: "This analysis was made by a test (mock) provider.",
      unsupported_findings: "Some findings have no evidence and are hidden.",
    },
    summary: {
      noSummary: "No summary is available yet.",
      unavailableBecause: (reason) => `The analysis is not available: ${reason}`,
      notSupported: "Not supported by evidence",
      topics: "Topics",
      sentiment: "Sentiment",
      evidence: "Evidence",
      redacted: "This summary is shown with personal data masked by the same detector as the transcript (structured data only).",
      original: "This summary is shown without masking.",
      analysisOnRedacted: "The analysis was produced from the masked transcript.",
    },
    outcome: {
      states: {
        explicit: "Stated outcome",
        inferred: "AI inference, not stated in the call",
        not_stated: "No outcome was stated in the call.",
        unavailable: "The outcome is not available yet.",
      },
      noEvidence: "No evidence",
    },
    qa: {
      counts: (supported, total) => `${supported} of ${total} findings have evidence`,
      none: "No findings with evidence.",
      types: { summary: "Summary", insight: "Key point", action_item: "Action item" },
    },
    evidence: {
      play: (time) => `Play evidence at ${time}`,
      showInTranscript: "Show in transcript",
      earlierRevision: "From an earlier transcript revision",
      speaker: "Speaker",
    },
    reviewActions: {
      labels: {
        correct_transcript: "Correct transcript",
        rename_speaker: "Rename speakers",
        edit_action_items: "Edit action items",
        share: "Share",
        export: "Export",
      },
      reasons: {
        permission_denied: "Your role cannot do this.",
        no_transcript: "Needs a transcript.",
        no_speakers: "No speakers were found.",
      },
      needsResults: "This opens when the meeting has results.",
    },
    advanced: "Transcript and tools",
    staleMarker: "Based on an earlier transcript",
  },
  fa: {
    loading: "در حال دریافت گزارش تماس",
    failed: "گزارش تماس دریافت نشد.",
    headings: {
      completeness: "کامل بودن گزارش",
      summary: "خلاصه",
      outcome: "نتیجه",
      qa: "یافته‌های کنترل کیفیت",
      reviewActions: "اقدام‌های بازبینی",
    },
    notices: {
      processing_in_progress: "پردازش هنوز در جریان است. این گزارش نهایی نیست.",
      processing_failed: "پردازش ناموفق بود. این گزارش ناقص است.",
      transcript_unavailable: "متن گفت‌وگو در دسترس نیست.",
      transcript_partial: "متن گفت‌وگو ناقص است.",
      analysis_unavailable: "تحلیلی در دسترس نیست.",
      analysis_partial: "تحلیل ناقص است.",
      analysis_stale: "متن گفت‌وگو پس از تحلیل تغییر کرده است. ممکن است نتایج به‌روز نباشند.",
      mock_analysis: "این تحلیل توسط ارائه‌دهندهٔ آزمایشی (mock) ساخته شده است.",
      unsupported_findings: "برخی یافته‌ها شاهدی ندارند و پنهان شده‌اند.",
    },
    summary: {
      noSummary: "هنوز خلاصه‌ای در دسترس نیست.",
      unavailableBecause: (reason) => `تحلیل در دسترس نیست: ${reason}`,
      notSupported: "شاهدی آن را تأیید نمی‌کند",
      topics: "موضوع‌ها",
      sentiment: "لحن",
      evidence: "شواهد",
      redacted: "این خلاصه با پوشاندن داده‌های شخصی توسط همان تشخیص‌دهندهٔ متن نمایش داده می‌شود (فقط داده‌های ساخت‌یافته).",
      original: "این خلاصه بدون پوشش نمایش داده می‌شود.",
      analysisOnRedacted: "تحلیل روی متن پوشانده‌شده انجام شده است.",
    },
    outcome: {
      states: {
        explicit: "نتیجهٔ بیان‌شده",
        inferred: "برداشت هوش مصنوعی؛ در تماس بیان نشده است",
        not_stated: "در تماس نتیجه‌ای بیان نشد.",
        unavailable: "نتیجه هنوز در دسترس نیست.",
      },
      noEvidence: "بدون شاهد",
    },
    qa: {
      counts: (supported, total) => `${supported} از ${total} یافته شاهد دارند`,
      none: "یافته‌ای با شاهد وجود ندارد.",
      types: { summary: "خلاصه", insight: "نکتهٔ کلیدی", action_item: "اقدام" },
    },
    evidence: {
      play: (time) => `پخش شاهد از ${time}`,
      showInTranscript: "نمایش در متن",
      earlierRevision: "از نسخهٔ قبلی متن گفت‌وگو",
      speaker: "گوینده",
    },
    reviewActions: {
      labels: {
        correct_transcript: "اصلاح متن گفت‌وگو",
        rename_speaker: "تغییر نام گوینده‌ها",
        edit_action_items: "ویرایش اقدام‌ها",
        share: "اشتراک‌گذاری",
        export: "خروجی گرفتن",
      },
      reasons: {
        permission_denied: "نقش شما اجازهٔ این کار را ندارد.",
        no_transcript: "به متن گفت‌وگو نیاز دارد.",
        no_speakers: "گوینده‌ای پیدا نشد.",
      },
      needsResults: "این بخش وقتی جلسه نتیجه داشته باشد باز می‌شود.",
    },
    advanced: "متن گفت‌وگو و ابزارها",
    staleMarker: "بر پایهٔ نسخهٔ پیشین متن گفت‌وگو",
  },
};
