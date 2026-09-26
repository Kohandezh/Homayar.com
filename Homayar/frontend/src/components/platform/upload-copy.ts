import type { UploadFailureKind } from "@/lib/upload-errors";

/**
 * Formats the file picker offers: the product's default ALLOWED_EXTENSIONS
 * (backend/config.py at d1cc5d4). The server's own setting decides; there is
 * no read API for it yet (Session B gap G3), so numeric size and duration
 * limits are not printed here — the server's refusal explains them.
 */
export const ACCEPTED_EXTENSIONS = [".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac", ".mp4", ".webm"];

export interface UploadCopy {
  title: string;
  chooseFile: string;
  changeFile: string;
  formats: string;
  meetingTitle: string;
  language: string;
  languageHint: string;
  submit: string;
  cancel: string;
  retry: string;
  retryIn: (clock: string) => string;
  chooseAgain: string;
  transferring: (percent: string) => string;
  transferNote: string;
  validating: string;
  cancelled: string;
  readOnly: string;
  verifyFirst: string;
  verifyLink: string;
  allowance: (remaining: string, reserved: string) => string;
  allowanceUnlimited: string;
  contactSales: string;
  openExisting: string;
  failures: Record<UploadFailureKind, string>;
  quotaDetail: (remaining: string, requested: string) => string;
}

export const UPLOAD_COPY: Record<"fa" | "en", UploadCopy> = {
  en: {
    title: "Upload a recording",
    chooseFile: "Choose a recording",
    changeFile: "Choose another file",
    formats: "Accepted formats: WAV, MP3, OGG, M4A, AAC, FLAC, MP4, WEBM.",
    meetingTitle: "Title",
    language: "Spoken language",
    languageHint: "A hint for transcription.",
    submit: "Upload",
    cancel: "Cancel upload",
    retry: "Try again",
    retryIn: (clock) => `You can try again in ${clock}.`,
    chooseAgain: "Choose the file again to start a new upload.",
    transferring: (percent) => `Sending the file: ${percent}`,
    transferNote: "This is the file transfer only; processing starts after the server accepts it.",
    validating: "File sent. The server is checking the recording…",
    cancelled: "The upload was cancelled. Trying again reuses the same upload key, so it cannot be processed twice.",
    readOnly: "Your role in this workspace is read-only, so it cannot upload recordings.",
    verifyFirst: "Verify your email or phone number before uploading.",
    verifyLink: "Enter a verification token",
    allowance: (remaining, reserved) =>
      reserved ? `Processing allowance left: ${remaining} (${reserved} reserved by uploads in progress).` : `Processing allowance left: ${remaining}.`,
    allowanceUnlimited: "This workspace has no audio processing limit.",
    contactSales: "Contact sales",
    openExisting: "Open the existing meeting",
    failures: {
      network: "The connection was lost during the upload. Trying again reuses the same upload key, so it cannot be processed twice.",
      cancelled: "The upload was cancelled.",
      too_large: "The file is larger than this service accepts. Upload a smaller or compressed recording.",
      too_long: "The recording is longer than this service accepts. Split it and upload the parts.",
      unsupported_type: "This file type is not supported, or its content does not match its extension.",
      unreadable: "The file could not be read as audio: it may be empty, damaged or have no audio track.",
      bad_filename: "The file name cannot be used. Rename the file with plain letters and numbers and try again.",
      verification_required: "Verify your email or phone number before uploading.",
      forbidden: "Your role in this workspace does not allow uploading.",
      conflict: "This upload key was already used for different content.",
      duplicate: "This recording was already uploaded to this workspace.",
      target_deleted: "This upload was received earlier, but its meeting has since been deleted.",
      quota: "The workspace's processing allowance does not cover this recording.",
      concurrency: "The workspace is already processing as many recordings as its plan allows. Try again when one finishes.",
      rate_limited: "Too many uploads in a short time.",
      temporary: "The service could not accept the file right now. Trying again reuses the same upload key, so it cannot be processed twice.",
      unknown: "The upload failed. Trying again reuses the same upload key, so it cannot be processed twice.",
    },
    quotaDetail: (remaining, requested) => `Remaining: ${remaining}; this recording needs ${requested}.`,
  },
  fa: {
    title: "بارگذاری فایل صوتی",
    chooseFile: "انتخاب فایل صوتی",
    changeFile: "انتخاب فایل دیگر",
    formats: "قالب‌های پذیرفته‌شده: WAV، MP3، OGG، M4A، AAC، FLAC، MP4، WEBM.",
    meetingTitle: "عنوان",
    language: "زبان گفت‌وگو",
    languageHint: "راهنمایی برای تبدیل گفتار به متن.",
    submit: "بارگذاری",
    cancel: "لغو بارگذاری",
    retry: "تلاش دوباره",
    retryIn: (clock) => `تا ${clock} دیگر می‌توانید دوباره تلاش کنید.`,
    chooseAgain: "برای بارگذاری تازه، فایل را دوباره انتخاب کنید.",
    transferring: (percent) => `در حال ارسال فایل: ${percent}`,
    transferNote: "این فقط ارسال فایل است؛ پردازش پس از پذیرش سرور آغاز می‌شود.",
    validating: "فایل ارسال شد. سرور در حال بررسی فایل صوتی است…",
    cancelled: "بارگذاری لغو شد. تلاش دوباره از همان کلید بارگذاری استفاده می‌کند، پس فایل دو بار پردازش نمی‌شود.",
    readOnly: "نقش شما در این فضای کاری فقط خواندنی است و امکان بارگذاری ندارد.",
    verifyFirst: "پیش از بارگذاری، ایمیل یا شمارهٔ موبایل خود را تأیید کنید.",
    verifyLink: "وارد کردن کد تأیید",
    allowance: (remaining, reserved) =>
      reserved ? `سهمیهٔ پردازش باقی‌مانده: ${remaining} (${reserved} برای بارگذاری‌های در جریان رزرو شده).` : `سهمیهٔ پردازش باقی‌مانده: ${remaining}.`,
    allowanceUnlimited: "این فضای کاری محدودیتی برای پردازش صوت ندارد.",
    contactSales: "تماس با فروش",
    openExisting: "باز کردن جلسهٔ موجود",
    failures: {
      network: "ارتباط در میانهٔ بارگذاری قطع شد. تلاش دوباره از همان کلید بارگذاری استفاده می‌کند، پس فایل دو بار پردازش نمی‌شود.",
      cancelled: "بارگذاری لغو شد.",
      too_large: "حجم فایل از حد مجاز این سرویس بیشتر است. فایل کوچک‌تر یا فشرده‌تری بارگذاری کنید.",
      too_long: "مدت فایل از حد مجاز این سرویس بیشتر است. آن را به چند بخش تقسیم کنید.",
      unsupported_type: "این نوع فایل پشتیبانی نمی‌شود یا محتوای آن با پسوندش هم‌خوان نیست.",
      unreadable: "فایل به‌عنوان صوت خوانده نشد؛ ممکن است خالی، آسیب‌دیده یا بدون صدا باشد.",
      bad_filename: "نام فایل قابل استفاده نیست. نام را با حروف و عدد ساده عوض کنید و دوباره تلاش کنید.",
      verification_required: "پیش از بارگذاری، ایمیل یا شمارهٔ موبایل خود را تأیید کنید.",
      forbidden: "نقش شما در این فضای کاری اجازهٔ بارگذاری ندارد.",
      conflict: "این کلید بارگذاری قبلاً برای محتوای دیگری استفاده شده است.",
      duplicate: "این فایل قبلاً در همین فضای کاری بارگذاری شده است.",
      target_deleted: "این بارگذاری قبلاً دریافت شده بود، اما جلسهٔ آن حذف شده است.",
      quota: "سهمیهٔ پردازش این فضای کاری برای این فایل کافی نیست.",
      concurrency: "این فضای کاری هم‌اکنون به سقف پردازش هم‌زمانِ طرح خود رسیده است. پس از پایان یکی از پردازش‌ها دوباره تلاش کنید.",
      rate_limited: "تعداد بارگذاری‌ها در زمان کوتاه بیش از حد مجاز است.",
      temporary: "سرویس فعلاً نتوانست فایل را بپذیرد. تلاش دوباره از همان کلید بارگذاری استفاده می‌کند، پس فایل دو بار پردازش نمی‌شود.",
      unknown: "بارگذاری انجام نشد. تلاش دوباره از همان کلید بارگذاری استفاده می‌کند، پس فایل دو بار پردازش نمی‌شود.",
    },
    quotaDetail: (remaining, requested) => `باقی‌مانده: ${remaining}؛ این فایل به ${requested} نیاز دارد.`,
  },
};
