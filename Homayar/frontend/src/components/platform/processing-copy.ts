/** Stage order of backend/jobs/states.py STAGE_ORDER at d1cc5d4. */
export const STAGE_ORDER = ["materialize", "asr", "diarization", "intelligence"] as const;

export interface ProcessingCopy {
  heading: string;
  stages: Record<string, string>;
  reasons: Record<string, string>;
  notStarted: string;
  waiting: string;
  running: (stage: string) => string;
  retryScheduled: (time: string) => string;
  lastAttemptFailed: (code: string) => string;
  attempt: (current: string, max: string) => string;
  serverSide: string;
  mock: string;
  reused: string;
  failedTitle: string;
  failedPermanent: string;
  deadLetterTitle: string;
  deadLetterBody: string;
  replay: string;
  replayHint: string;
  askAdmin: string;
  replayQueued: string;
  replayErrors: Record<string, string>;
  replayFailed: string;
  cancelled: string;
  partial: string;
  uploadReplay: string;
  deleted: string;
  backToMeetings: string;
}

export const PROCESSING_COPY: Record<"fa" | "en", ProcessingCopy> = {
  en: {
    heading: "Processing",
    stages: {
      materialize: "Preparing the recording",
      asr: "Speech to text",
      diarization: "Separating speakers",
      intelligence: "Summary and analysis",
    },
    reasons: {
      empty_transcript: "No speech was recognised in the recording.",
      no_transcript: "There was no transcript to analyse.",
      diarization_unavailable: "Speaker separation is not available for this recording.",
      provider_error: "The processing service returned an error.",
      processing_error: "The stage stopped with a processing error.",
      not_reached: "Not reached because an earlier stage stopped.",
      checkpoint_reused: "Reused from an earlier attempt; the service was not called again.",
    },
    notStarted: "Not started",
    waiting: "Waiting in the processing queue.",
    running: (stage) => `Now running: ${stage}.`,
    retryScheduled: (time) => `The next automatic attempt is scheduled for ${time}.`,
    lastAttemptFailed: (code) => `The last attempt failed (${code}).`,
    attempt: (current, max) => `Attempt ${current} of ${max}`,
    serverSide: "Processing runs on the server; you can leave this page and come back.",
    mock: "Test (mock) provider output — not a real analysis",
    reused: "reused",
    failedTitle: "Processing failed",
    failedPermanent: "This recording cannot be processed as it is; trying again would fail the same way.",
    deadLetterTitle: "Processing stopped after repeated failures",
    deadLetterBody: "The automatic retries are used up.",
    replay: "Retry processing",
    replayHint: "A new attempt needs available processing allowance.",
    askAdmin: "A workspace owner or administrator can retry processing.",
    replayQueued: "Processing was queued again.",
    replayErrors: {
      job_not_replayable: "This job can no longer be retried.",
      replay_limit_reached: "The retry limit for this job has been reached.",
      meeting_deleted: "The meeting was deleted, so it cannot be processed again.",
      retention_expired: "The recording's retention period has ended, so it cannot be processed again.",
      recording_missing: "The recording is no longer available, so it cannot be processed again.",
      audio_quota_exceeded: "The processing allowance does not cover another attempt.",
      concurrent_job_limit_reached: "The workspace is already processing as many recordings as its plan allows.",
    },
    replayFailed: "The retry could not be started.",
    cancelled: "Processing was cancelled.",
    partial: "Processing finished with gaps; the stages below show what is missing.",
    uploadReplay: "This recording had already been received; this is the existing meeting.",
    deleted: "This meeting was deleted or you no longer have access to it. Nothing from it is shown.",
    backToMeetings: "Back to meetings",
  },
  fa: {
    heading: "پردازش",
    stages: {
      materialize: "آماده‌سازی فایل",
      asr: "تبدیل گفتار به متن",
      diarization: "تفکیک گوینده‌ها",
      intelligence: "خلاصه و تحلیل",
    },
    reasons: {
      empty_transcript: "در این فایل گفتاری تشخیص داده نشد.",
      no_transcript: "متنی برای تحلیل وجود نداشت.",
      diarization_unavailable: "تفکیک گوینده برای این فایل در دسترس نیست.",
      provider_error: "سرویس پردازش خطا برگرداند.",
      processing_error: "این مرحله با خطای پردازش متوقف شد.",
      not_reached: "به این مرحله نرسید، چون مرحلهٔ قبلی متوقف شد.",
      checkpoint_reused: "از تلاش قبلی استفاده شد؛ سرویس دوباره فراخوانی نشد.",
    },
    notStarted: "شروع نشده",
    waiting: "در صف پردازش است.",
    running: (stage) => `مرحلهٔ در حال اجرا: ${stage}.`,
    retryScheduled: (time) => `تلاش خودکار بعدی برای ساعت ${time} زمان‌بندی شده است.`,
    lastAttemptFailed: (code) => `تلاش قبلی ناموفق بود (${code}).`,
    attempt: (current, max) => `تلاش ${current} از ${max}`,
    serverSide: "پردازش روی سرور انجام می‌شود؛ می‌توانید این صفحه را ببندید و بعداً برگردید.",
    mock: "خروجی ارائه‌دهندهٔ آزمایشی (mock) — تحلیل واقعی نیست",
    reused: "استفادهٔ دوباره",
    failedTitle: "پردازش ناموفق بود",
    failedPermanent: "این فایل با وضعیت فعلی قابل پردازش نیست؛ تلاش دوباره هم به همین نتیجه می‌رسد.",
    deadLetterTitle: "پردازش پس از چند بار خطا متوقف شد",
    deadLetterBody: "تلاش‌های خودکار تمام شده است.",
    replay: "پردازش دوباره",
    replayHint: "تلاش تازه به سهمیهٔ پردازش آزاد نیاز دارد.",
    askAdmin: "مالک یا مدیر فضای کاری می‌تواند پردازش را دوباره آغاز کند.",
    replayQueued: "پردازش دوباره در صف قرار گرفت.",
    replayErrors: {
      job_not_replayable: "این پردازش دیگر قابل تکرار نیست.",
      replay_limit_reached: "سقف تکرار این پردازش پر شده است.",
      meeting_deleted: "جلسه حذف شده است و دوباره پردازش نمی‌شود.",
      retention_expired: "دورهٔ نگهداری فایل تمام شده است و دوباره پردازش نمی‌شود.",
      recording_missing: "فایل صوتی دیگر در دسترس نیست و دوباره پردازش نمی‌شود.",
      audio_quota_exceeded: "سهمیهٔ پردازش برای تلاش دیگری کافی نیست.",
      concurrent_job_limit_reached: "این فضای کاری هم‌اکنون به سقف پردازش هم‌زمانِ طرح خود رسیده است.",
    },
    replayFailed: "پردازش دوباره آغاز نشد.",
    cancelled: "پردازش لغو شد.",
    partial: "پردازش با کمبود به پایان رسید؛ مراحل زیر نشان می‌دهند چه چیزی در دسترس نیست.",
    uploadReplay: "این فایل قبلاً دریافت شده بود؛ این همان جلسهٔ موجود است.",
    deleted: "این جلسه حذف شده یا دیگر به آن دسترسی ندارید. چیزی از آن نمایش داده نمی‌شود.",
    backToMeetings: "بازگشت به جلسه‌ها",
  },
};
