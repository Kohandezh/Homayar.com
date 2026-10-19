/**
 * Every string of the AI policy page, one copy object in both console
 * languages. The English strings are the contract the tests look for; keep
 * the Persian translations natural, not word by word.
 */
import type { AiCloudTextRule, AiEndpointKind } from "@/types/ai-policy";
import type { Lang } from "@/lib/i18n-context";

export const AI_POLICY_STAGES = ["asr", "diarization", "intelligence"] as const;
export type AiStageName = (typeof AI_POLICY_STAGES)[number];

export const ENDPOINT_KINDS = ["in_process", "private", "cloud"] as const;

/** Where a stage may run, in plain words; an empty list is a turned-off stage. */
export function kindListText(copy: AiPolicyCopy, kinds: readonly AiEndpointKind[]): string {
  const labels = ENDPOINT_KINDS.filter((kind) => kinds.includes(kind)).map((kind) => copy.kinds[kind]);
  return labels.length ? labels.join(", ") : copy.turnedOff;
}

export interface AiPolicyCopy {
  heading: string;
  intro: string;
  stages: Record<AiStageName, string>;
  kinds: Record<AiEndpointKind, string>;
  turnedOff: string;
  fallbackAllowed: string;
  fallbackNotAllowed: string;
  anyManifest: string;
  manifestsLabel: string;
  cloudText: Record<AiCloudTextRule, string>;
  badgeDefault: string;
  badgeCustom: (version: string) => string;
  reasonLabel: string;
  changedLabel: string;
  loading: string;
  loadError: string;
  tryAgain: string;
  editPolicy: string;
  ownerHint: string;
  /** The edit form. */
  cancel: string;
  presets: string;
  presetLocalOnly: string;
  presetPrivate: string;
  regions: string;
  allowFallback: string;
  textToCloud: string;
  redactedText: string;
  originalText: string;
  reasonForChange: string;
  savePolicy: string;
  savingPolicy: string;
  savedVersion: (version: string) => string;
  nothingChanged: string;
  conflict: string;
  reloadPolicy: string;
  reloadFailed: string;
  putForbidden: string;
  putInvalid: string;
  putFailed: string;
  cloudNeedsRegion: string;
  invalidRegion: string;
  reasonTooShort: string;
  /** The version history. */
  history: string;
  historyIntro: string;
  columns: { version: string; reason: string; changedBy: string; date: string };
  actions: string;
  noVersions: string;
  historyError: string;
  compare: (version: string) => string;
  compareHeading: (version: string) => string;
  compareSame: (version: string) => string;
  compareSelected: string;
  compareCurrent: string;
  /** Field names of the comparison, in plain words. */
  fields: {
    endpoint_kinds: string;
    regions: string;
    manifests: string;
    fallback: string;
    cloud_text: string;
  };
  /** Values of the comparison, in plain words. */
  allowed: string;
  notAllowed: string;
  redacted: string;
  original: string;
}

export const AI_POLICY_COPY: Record<Lang, AiPolicyCopy> = {
  en: {
    heading: "AI policy",
    intro:
      "Where each stage of processing may run: on this workspace's own servers, on a private endpoint, or at a cloud provider. Versions are kept, so every change can be compared with the current policy.",
    stages: {
      asr: "Speech to text",
      diarization: "Speaker separation",
      intelligence: "Summary and analysis",
    },
    kinds: {
      in_process: "On your servers only",
      private: "Private endpoint",
      cloud: "Cloud provider",
    },
    turnedOff: "Turned off",
    fallbackAllowed: "Fallback: allowed",
    fallbackNotAllowed: "Fallback: not allowed",
    anyManifest: "Any approved model",
    manifestsLabel: "Approved models",
    cloudText: {
      redacted: "Cloud providers receive redacted text",
      original: "Cloud providers receive the original text",
    },
    badgeDefault: "Built-in default",
    badgeCustom: (version) => `Custom, version ${version}`,
    reasonLabel: "Reason",
    changedLabel: "Changed",
    loading: "Loading the AI policy",
    loadError: "Could not load the AI policy.",
    tryAgain: "Try again",
    editPolicy: "Edit policy",
    ownerHint: "Only the workspace owner can change the AI policy.",
    cancel: "Cancel",
    presets: "Presets",
    presetLocalOnly: "Local only",
    presetPrivate: "Private",
    regions: "Regions",
    allowFallback: "Allow fallback",
    textToCloud: "Text sent to cloud providers",
    redactedText: "Redacted text",
    originalText: "Original text",
    reasonForChange: "Reason for the change",
    savePolicy: "Save policy",
    savingPolicy: "Saving policy…",
    savedVersion: (version) => `AI policy saved as version ${version}.`,
    nothingChanged: "Nothing changed. The policy is already like this.",
    conflict: "Someone else changed the AI policy while you were editing.",
    reloadPolicy: "Reload policy",
    reloadFailed: "Could not reload the AI policy.",
    putForbidden: "Only the workspace owner can change the AI policy.",
    putInvalid: "The server did not accept this policy. Check the values and try again.",
    putFailed: "Could not save the AI policy.",
    cloudNeedsRegion: "Add at least one region to allow a cloud provider.",
    invalidRegion: "Use lowercase letters, digits and dashes for a region, for example eu-west-1.",
    reasonTooShort: "Write a reason of at least 3 characters.",
    history: "Version history",
    historyIntro: "Every saved version, newest first.",
    columns: { version: "Version", reason: "Reason", changedBy: "Changed by", date: "Date" },
    actions: "Actions",
    noVersions: "No saved versions yet. The built-in default is in use.",
    historyError: "Could not load the version history.",
    compare: (version) => `Compare version ${version}`,
    compareHeading: (version) => `Version ${version} compared with the current policy`,
    compareSame: (version) => `Version ${version} is the same as the current policy.`,
    compareSelected: "Selected version",
    compareCurrent: "Current policy",
    fields: {
      endpoint_kinds: "Where it may run",
      regions: "Regions",
      manifests: "Approved models",
      fallback: "Fallback",
      cloud_text: "Text sent to cloud providers",
    },
    allowed: "allowed",
    notAllowed: "not allowed",
    redacted: "redacted",
    original: "original",
  },
  fa: {
    heading: "سیاست هوش مصنوعی",
    intro:
      "هر مرحلهٔ پردازش کجا می‌تواند اجرا شود: روی سرورهای خود این فضای کاری، روی یک نقطهٔ پایانی خصوصی، یا نزد ارائه‌دهندهٔ ابری. نسخه‌ها نگه داشته می‌شوند تا هر تغییر با سیاست فعلی قابل مقایسه باشد.",
    stages: {
      asr: "تبدیل گفتار به متن",
      diarization: "تفکیک گوینده‌ها",
      intelligence: "خلاصه و تحلیل",
    },
    kinds: {
      in_process: "فقط روی سرورهای شما",
      private: "نقطهٔ پایانی خصوصی",
      cloud: "ارائه‌دهندهٔ ابری",
    },
    turnedOff: "خاموش است",
    fallbackAllowed: "در صورت خطا، جایگزین مجاز است",
    fallbackNotAllowed: "در صورت خطا، جایگزین مجاز نیست",
    anyManifest: "هر مدل تأییدشده",
    manifestsLabel: "مدل‌های تأییدشده",
    cloudText: {
      redacted: "ارائه‌دهنده‌های ابری متن پنهان‌شده دریافت می‌کنند",
      original: "ارائه‌دهنده‌های ابری متن اصلی را دریافت می‌کنند",
    },
    badgeDefault: "پیش‌فرض داخلی",
    badgeCustom: (version) => `سفارشی، نسخهٔ ${version}`,
    reasonLabel: "دلیل",
    changedLabel: "تاریخ تغییر",
    loading: "در حال دریافت سیاست هوش مصنوعی",
    loadError: "دریافت سیاست هوش مصنوعی ناموفق بود.",
    tryAgain: "تلاش دوباره",
    editPolicy: "ویرایش سیاست",
    ownerHint: "فقط مالک فضای کاری می‌تواند سیاست هوش مصنوعی را تغییر دهد.",
    cancel: "انصراف",
    presets: "پیش‌تنظیم‌ها",
    presetLocalOnly: "فقط محلی",
    presetPrivate: "خصوصی",
    regions: "منطقه‌ها",
    allowFallback: "اجازهٔ جایگزین در صورت خطا",
    textToCloud: "متن ارسالی به ارائه‌دهنده‌های ابری",
    redactedText: "متن پنهان‌شده",
    originalText: "متن اصلی",
    reasonForChange: "دلیل این تغییر",
    savePolicy: "ذخیرهٔ سیاست",
    savingPolicy: "در حال ذخیرهٔ سیاست…",
    savedVersion: (version) => `سیاست هوش مصنوعی به‌عنوان نسخهٔ ${version} ذخیره شد.`,
    nothingChanged: "چیزی تغییر نکرد؛ سیاست از قبل همین بود.",
    conflict: "در حال ویرایش شما، شخص دیگری سیاست هوش مصنوعی را تغییر داد.",
    reloadPolicy: "بارگذاری دوبارهٔ سیاست",
    reloadFailed: "بارگذاری دوبارهٔ سیاست هوش مصنوعی ناموفق بود.",
    putForbidden: "فقط مالک فضای کاری می‌تواند سیاست هوش مصنوعی را تغییر دهد.",
    putInvalid: "سرور این سیاست را نپذیرفت. مقادیر را بررسی کنید و دوباره تلاش کنید.",
    putFailed: "ذخیرهٔ سیاست هوش مصنوعی ناموفق بود.",
    cloudNeedsRegion: "برای اجازهٔ ارائه‌دهندهٔ ابری، دست‌کم یک منطقه وارد کنید.",
    invalidRegion: "برای منطقه از حروف کوچک، رقم و خط تیره استفاده کنید؛ برای نمونه eu-west-1.",
    reasonTooShort: "دلیلی با دست‌کم ۳ نویسه بنویسید.",
    history: "تاریخچهٔ نسخه‌ها",
    historyIntro: "همهٔ نسخه‌های ذخیره‌شده، از تازه به قدیم.",
    columns: { version: "نسخه", reason: "دلیل", changedBy: "تغییردهنده", date: "تاریخ" },
    actions: "عملیات",
    noVersions: "هنوز نسخه‌ای ذخیره نشده است. سیاست پیش‌فرض داخلی در حال استفاده است.",
    historyError: "دریافت تاریخچهٔ نسخه‌ها ناموفق بود.",
    compare: (version) => `مقایسهٔ نسخهٔ ${version}`,
    compareHeading: (version) => `نسخهٔ ${version} در مقایسه با سیاست فعلی`,
    compareSame: (version) => `نسخهٔ ${version} با سیاست فعلی یکسان است.`,
    compareSelected: "نسخهٔ انتخاب‌شده",
    compareCurrent: "سیاست فعلی",
    fields: {
      endpoint_kinds: "کجا می‌تواند اجرا شود",
      regions: "منطقه‌ها",
      manifests: "مدل‌های تأییدشده",
      fallback: "جایگزین در صورت خطا",
      cloud_text: "متن ارسالی به ارائه‌دهنده‌های ابری",
    },
    allowed: "مجاز",
    notAllowed: "مجاز نیست",
    redacted: "پنهان‌شده",
    original: "اصلی",
  },
};
